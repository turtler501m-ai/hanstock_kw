"""Approval claiming and execution service extracted from dashboard core."""

def _refresh_dependencies() -> None:
    from src.dashboard import core
    protected = {name for name in globals() if name.startswith("_approval") or name in {
        "_refresh_dependencies", "_load_pending_approval", "_claim_pending_approval",
        "_current_holding_qty_from_balance", "_pending_approval_ids",
        "_is_approval_already_claimed", "_auto_approve_pending_approvals",
        "_approve_pending_approval", "_approve_pending_approval_serialized",
        "_buy_approval_capacity_decision", "_enforce_buy_position_limit",
        "_additional_buy_strategy_id",
    }}
    globals().update({name: value for name, value in vars(core).items() if name not in protected})


def _dependency(name: str, local):
    from src.dashboard import core
    candidate = getattr(core, name, None)
    return local if getattr(candidate, "_approval_service_wrapper", False) is True else candidate or local


def _is_tick_size_error(result: dict) -> bool:
    message = " ".join(
        str(result.get(key) or "")
        for key in ("msg1", "message", "response_msg")
    )
    return "호가단위" in message


_AMBIGUOUS_SUBMISSION_MARKERS = (
    "connection aborted", "connection reset", "remote disconnected",
    "remotedisconnected", "remote end closed", "readtimeout",
    "connecttimeout", "timed out", "timeout", "시간 초과",
)


def _is_ambiguous_submission_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    normalized = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in normalized for marker in _AMBIGUOUS_SUBMISSION_MARKERS)


def _load_pending_approval(approval_id: int) -> dict:
    item = _approval_by_id(approval_id)
    if not item:
        raise HTTPException(status_code=404, detail="approval not found")
    if item["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"approval is already {item['status']}")
    return item


def _claim_pending_approval(approval_id: int) -> dict:
    item = _load_pending_approval(approval_id)
    now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
    with trader.connect_db() as conn:
        cursor = conn.execute(
            """
            UPDATE approvals
            SET status = 'executing', response_msg = 'Submitting order to broker', updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, approval_id),
        )
    if cursor.rowcount != 1:
        current = _approval_by_id(approval_id)
        if current is None:
            raise HTTPException(status_code=404, detail="approval not found")
        raise HTTPException(status_code=409, detail=f"approval is already {current['status']}")
    return item


def _approval_response_msg(result: dict, *, ok: bool) -> str:
    response_msg = str(result.get("msg1", ""))
    if ok and not trader.runtime_flags().dry_run and trader.runtime_flags().trading_env == "demo":
        response_msg = f"{response_msg} (키움 모의투자 주문 접수 완료, 체결 여부는 주문내역 동기화 후 확인)"
    return response_msg


def _current_holding_qty_from_balance(api, symbol: str) -> int:
    try:
        parsed = _parse_balance(_get_balance_data(api, allow_cache=True))
    except DashboardOperationError:
        return 0
    for holding in parsed.get("holdings", []):
        if str(holding.get("symbol") or "") == str(symbol):
            return _to_int(holding.get("qty"))
    return 0


def _buy_approval_capacity_decision(
    *,
    approval_id: int,
    symbol: str,
    held_symbols: set[str],
    active_buy_symbols: set[str],
    pending_buys: list[tuple[int, str]],
    max_positions: int,
) -> tuple[bool, str]:
    target = str(symbol or "").strip()
    occupied = {str(value) for value in held_symbols | active_buy_symbols if str(value)}
    if target in occupied:
        return False, f"duplicate buy exposure already exists for {target}"
    available_slots = max(0, int(max_positions) - len(occupied))
    if available_slots <= 0:
        return False, f"maximum positions reached ({len(occupied)}/{max_positions})"

    eligible_ids: list[int] = []
    seen = set(occupied)
    for pending_id, pending_symbol in pending_buys:
        normalized = str(pending_symbol or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        eligible_ids.append(int(pending_id))
        if len(eligible_ids) >= available_slots:
            break
    if int(approval_id) not in eligible_ids:
        return False, f"buy approval exceeds remaining position slots ({available_slots})"
    return True, ""


_ADDITIONAL_BUY_STRATEGIES = {"ai_rebalance", "rsi_limit_strategy"}


def _additional_buy_strategy_id(pending: dict) -> str | None:
    """Return the opt-in strategy allowed to add to an existing holding."""
    strategy_id = str(pending.get("strategy_id") or "").strip()
    if not strategy_id and str(pending.get("source") or "").strip() == "ai-allocation":
        strategy_id = "ai_rebalance"
    return strategy_id if strategy_id in _ADDITIONAL_BUY_STRATEGIES else None


def _enforce_buy_position_limit(approval_id: int, pending: dict) -> None:
    api = _get_api()
    parsed = (
        _parse_balance(_get_balance_data(api, allow_cache=True))
        if hasattr(api, "get_balance")
        else {"holdings": []}
    )
    held_symbols = {
        str(row.get("symbol") or "").strip()
        for row in parsed.get("holdings", [])
        if str(row.get("symbol") or "").strip()
    }
    today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
    with trader.connect_db() as conn:
        active_buy_symbols = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT symbol FROM trades
                WHERE action = 'buy'
                  AND order_status IN ('submitted', 'open', 'partial')
                  AND ts >= ?
                """,
                (today,),
            ).fetchall()
            if str(row[0] or "")
        }
        pending_buys = [
            (int(row[0]), str(row[1] or ""))
            for row in conn.execute(
                """
                SELECT id, symbol FROM approvals
                WHERE action = 'buy'
                  AND status IN ('pending', 'executing')
                  AND created_at >= ?
                ORDER BY id ASC
                """,
                (today,),
            ).fetchall()
        ]

    target = str(pending.get("symbol") or "").strip()
    additional_buy_strategy = _additional_buy_strategy_id(pending)
    if additional_buy_strategy and target in held_symbols:
        # Scale-ins do not consume another position slot. Limit each strategy
        # to one outstanding add-on order per symbol; monetary exposure is
        # capped by the strategy-specific sizing policy when the order is built.
        earlier_pending_same_symbol = any(
            int(pending_id) < int(approval_id)
            and str(pending_symbol or "").strip() == target
            for pending_id, pending_symbol in pending_buys
        )
        if target not in active_buy_symbols and not earlier_pending_same_symbol:
            return
        reason = (
            f"{additional_buy_strategy} additional buy rejected: "
            f"an outstanding buy already exists for {target}"
        )
        now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
        with trader.connect_db() as conn:
            conn.execute(
                "UPDATE approvals SET status = 'rejected', response_msg = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (reason, now, approval_id),
            )
        raise HTTPException(status_code=409, detail=reason)

    allowed, reason = _buy_approval_capacity_decision(
        approval_id=approval_id,
        symbol=str(pending.get("symbol") or ""),
        held_symbols=held_symbols,
        active_buy_symbols=active_buy_symbols,
        pending_buys=pending_buys,
        max_positions=int(getattr(trader.get_settings(), "max_positions", 0)),
    )
    if allowed:
        return
    now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
    message = f"리스크 제한으로 매수 거절: {reason}"
    with trader.connect_db() as conn:
        conn.execute(
            """
            UPDATE approvals SET status = 'rejected', response_msg = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (message, now, approval_id),
        )
    raise HTTPException(status_code=409, detail=message)


def _pending_approval_ids(limit: int = 200, *, exclude_sources: set[str] | None = None) -> list[int]:
    _init_approval_db()
    with trader.connect_db() as conn:

        conn.row_factory = sqlite3.Row
        if exclude_sources:
            placeholders = ", ".join("?" for _ in exclude_sources)
            rows = conn.execute(
                f"""
                SELECT id FROM approvals
                WHERE status = 'pending'
                  AND COALESCE(source, '') NOT IN ({placeholders})
                ORDER BY id ASC
                LIMIT ?
                """,
                (*sorted(exclude_sources), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM approvals WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
    return [int(row["id"]) for row in rows]


def _is_approval_already_claimed(exc: Exception) -> bool:
    detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    return "approval is already" in detail


def _auto_approve_pending_approvals(limit: int = 200) -> list[dict]:
    from src.dashboard import core

    results = []
    for approval_id in core._pending_approval_ids(
        limit, exclude_sources=AUTO_APPROVAL_EXCLUDED_SOURCES
    ):
        try:
            results.append(core._approve_pending_approval(approval_id, "자동승인"))
        except Exception as exc:
            if _is_approval_already_claimed(exc):
                logger.debug(f"auto approval skipped approval_id={approval_id}: {exc}")
                continue
            logger.warning(f"auto approval failed for approval_id={approval_id}: {exc}")
            continue
    return results


def _approve_pending_approval(approval_id: int, approval_label: str = "수동승인") -> dict:
    # Explicit sell-all batches, the periodic sweeper, scheduler jobs, and
    # manual approvals can all reach this function concurrently. Serialize the
    # Broker-facing path serializes approval orders.
    with _approval_submission_lock:
        return _approve_pending_approval_serialized(approval_id, approval_label)


def _approve_pending_approval_serialized(
    approval_id: int,
    approval_label: str = "수동승인",
    *,
    approval: dict | None = None,
) -> dict:
    from src.online_access import is_online_access_blocked

    if is_online_access_blocked():
        raise HTTPException(
            status_code=409,
            detail="Online access is blocked. Approval remains pending.",
        )
    pending = approval or _dependency(
        "_load_pending_approval", _load_pending_approval
    )(approval_id)
    created_at = str(pending.get("created_at") or "").strip()
    today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
    if created_at and created_at[:10] != today:
        now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
        with trader.connect_db() as conn:
            conn.execute(
                "UPDATE approvals SET status='expired', response_msg=?, updated_at=? "
                "WHERE id=? AND status='pending'",
                ("Approval expired at the end of its trading day", now, approval_id),
            )
        raise HTTPException(
            status_code=409,
            detail="Approval expired at the end of its trading day. Create a fresh order.",
        )
    if (
        str(pending.get("action") or "").lower() == "buy"
        and Path(".runtime/kill_switch.json").exists()
    ):
        raise HTTPException(
            status_code=409,
            detail="Kill switch is active. Buy approval remains pending.",
        )
    if str(pending.get("action") or "").lower() == "buy":
        _enforce_buy_position_limit(approval_id, pending)
    if pending.get("managed_order_id"):
        from src.strategy.autonomy.ai_stock_integration import (
            approve_managed_ai_stock_order,
        )

        try:
            return approve_managed_ai_stock_order(approval_id)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"managed AI-stock approval failed closed: {exc}",
            ) from exc
    item = _dependency("_claim_pending_approval", _claim_pending_approval)(approval_id)
    result: dict = {}
    status = "failed"
    response_msg = "Order submission did not complete"
    submission_started = False
    pre_order_qty = 0
    try:
        api = _get_api()
        pre_order_qty = _dependency(
            "_current_holding_qty_from_balance", _current_holding_qty_from_balance
        )(api, item["symbol"])
        submission_started = True
        result = api.place_order(item["symbol"], item["action"], item["price"], item["qty"])
        if result.get("rt_cd") != "0" and _is_tick_size_error(result):
            from src.strategy.seven_split import adjust_tick_size

            adjusted_price = adjust_tick_size(int(item["price"]))
            if adjusted_price > 0 and adjusted_price != int(item["price"]):
                logger.warning(
                    "approval order tick-size retry approval_id={} symbol={} price={} adjusted_price={}",
                    approval_id,
                    item["symbol"],
                    item["price"],
                    adjusted_price,
                )
                item["price"] = adjusted_price
                result = api.place_order(
                    item["symbol"], item["action"], adjusted_price, item["qty"]
                )
        ok = result.get("rt_cd") == "0"
        status = "executed" if ok else "failed"
        response_msg = _dependency(
            "_approval_response_msg", _approval_response_msg
        )(result, ok=ok)
        if False:  # legacy non-English broker note disabled
            response_msg = f"{response_msg} (주문 접수 완료 - 실제 체결 여부는 HTS/MTS에서 확인 필요)"
        trader.save_trade(
            item["symbol"],
            item["name"],
            item["action"],
            item["qty"],
            item["price"],
            item["reason"],

            ok,
            trader.runtime_flags().order_submission_enabled,
            broker_result=result,
            order_status="submitted" if ok and trader.runtime_flags().order_submission_enabled else "simulated" if ok else "failed",
            response_msg=response_msg,
            filled_qty=0 if ok and trader.runtime_flags().order_submission_enabled else item["qty"] if ok else 0,
            filled_price=0 if ok and trader.runtime_flags().order_submission_enabled else item["price"] if ok else 0,
            pre_order_qty=pre_order_qty,
            strategy_id=item.get("strategy_id"),
            strategy_version=_to_int(item.get("strategy_version")) or None,
            profile_hash=item.get("profile_hash"),
            source_approval_id=approval_id,
        )
    except Exception as e:
        ambiguous = submission_started and _is_ambiguous_submission_exception(e)
        status = "broker_unknown" if ambiguous else "failed"
        response_msg = (
            f"Broker response was not received; verify Kiwoom order history before any retry. ({e})"
            if ambiguous
            else str(e)
        )
        if ambiguous:
            trader.save_trade(
                item["symbol"], item["name"], item["action"], item["qty"],
                item["price"], item["reason"], False,
                trader.runtime_flags().order_submission_enabled,
                broker_result={}, order_status="broker_unknown",
                response_msg=response_msg, filled_qty=0, filled_price=0,
                pre_order_qty=pre_order_qty, strategy_id=item.get("strategy_id"),
                strategy_version=_to_int(item.get("strategy_version")) or None,
                profile_hash=item.get("profile_hash"), source_approval_id=approval_id,
            )
        logger.warning(f"approval order submission failed approval_id={approval_id}: {e}")

    now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
    with trader.connect_db() as conn:
        conn.execute(
            "UPDATE approvals SET status = ?, response_msg = ?, updated_at = ? WHERE id = ?",
            (status, response_msg, now, approval_id),
        )

    # Slack 알림
    try:
        indicators = {"rsi": "-", "sma20": 0, "sma60": 0, "rt": 0}
        _slack_order(
            item["name"], item["symbol"], item["action"],
            item["qty"], item["price"],
            f"[대시보드 {approval_label}] {item.get('reason', '')}",
            status == "executed",
            indicators,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(f"Failed to send approval notification: {exc}")

    # 주문이 실제 체결/제출되었으면 잔고가 바뀌므로 잔고·파생 탭 스냅샷을 무효화해
    # 다음 read에서 최신 상태를 다시 받아오게 한다.
    if status == "executed":
        _clear_balance_cache()

    return {"id": approval_id, "status": status, "response_msg": response_msg}



import time

_cloud_trades_cache = None

_cloud_trades_cache_time = 0
