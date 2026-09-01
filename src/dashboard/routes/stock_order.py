"""Order and trade HTTP handlers extracted from the legacy stock route module."""

import functools
import inspect
import threading
import time

from fastapi import APIRouter
from src.dashboard.routes import stock as _stock

_ROUTE_OWNED_STATE = {
    "_trade_sync_lock",
    "_trade_sync_thread",
}

def _refresh_legacy_dependencies() -> None:
    globals().update({
        name: value for name, value in vars(_stock).items()
        if name not in {"router", "_refresh_legacy_dependencies", "_CompatRouter", "_stock"}
        and name not in _ROUTE_OWNED_STATE
        and not name.startswith("__")
    })


class _CompatRouter(APIRouter):
    def api_route(self, path: str, **kwargs):
        register = super().api_route(path, **kwargs)
        def decorator(endpoint):
            if inspect.iscoroutinefunction(endpoint):
                @functools.wraps(endpoint)
                async def dispatch(*args, **inner_kwargs):
                    _refresh_legacy_dependencies()
                    return await endpoint(*args, **inner_kwargs)
            else:
                @functools.wraps(endpoint)
                def dispatch(*args, **inner_kwargs):
                    _refresh_legacy_dependencies()
                    return endpoint(*args, **inner_kwargs)
            register(dispatch)
            return endpoint
        return decorator


_refresh_legacy_dependencies()
router = _CompatRouter(tags=["stock", "stock-order"])
_trade_sync_lock = threading.Lock()
_trade_sync_thread: threading.Thread | None = None

_ATTRIBUTED_BALANCE_SYNC_REASON = "증권사 잔고 전략귀속 동기화"


def _record_reconciliation_issue(
    *, symbol: str, broker_qty: int, internal_qty: int, reason: str, snapshot: dict,
) -> int:
    import json
    from src.application.orders.identity import broker_account_scope_key

    difference = int(broker_qty) - int(internal_qty)
    now_value = trader.datetime.now(trader.KST)
    now = now_value.strftime("%Y-%m-%d %H:%M:%S")
    account_key = broker_account_scope_key("KR")
    with trader.connect_db() as conn:
        existing = conn.execute(
            """SELECT id FROM reconciliation_adjustments
               WHERE account_key=? AND market='KR' AND symbol=? AND status='open'
                 AND broker_qty=? AND internal_qty=? AND difference_qty=?
               ORDER BY id DESC LIMIT 1""",
            (account_key, symbol, broker_qty, internal_qty, difference),
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = conn.execute(
            """INSERT INTO reconciliation_adjustments
               (account_key,market,symbol,broker_qty,internal_qty,difference_qty,
                reason,status,snapshot_json,created_at)
               VALUES(?,'KR',?,?,?,?,?,'open',?,?)""",
            (
                account_key, symbol, broker_qty, internal_qty, difference, reason,
                json.dumps(snapshot, ensure_ascii=False, default=str), now,
            ),
        )
        return int(cursor.lastrowid)


@router.get("/api/orders")
def unified_orders(status: str = "", limit: int = 100, offset: int = 0):
    """Read the unified ledger; legacy tables remain compatibility projections."""
    from src.application.orders.repository import OrderLedgerRepository

    statuses = tuple(value.strip() for value in status.split(",") if value.strip())
    items = OrderLedgerRepository(trader.connect_db).list_orders(
        statuses=statuses, limit=limit, offset=offset
    )
    return {"items": items, "limit": min(500, max(1, limit)), "offset": max(0, offset)}


@router.get("/api/orders/{order_id}")
def unified_order_detail(order_id: int):
    from fastapi import HTTPException
    from src.application.orders.repository import OrderLedgerRepository

    item = OrderLedgerRepository(trader.connect_db).detail(order_id)
    if not item:
        raise HTTPException(status_code=404, detail="order not found")
    return item


@router.get("/api/positions")
def unified_positions(market: str = "KR"):
    from src.application.orders.repository import OrderLedgerRepository

    return {
        "items": OrderLedgerRepository(trader.connect_db).list_positions(market=market),
        "market": market,
        "source": "verified_fills",
    }


@router.post("/api/orders/{order_id}/reconcile")
def reconcile_unified_order(order_id: int):
    from fastapi import HTTPException
    from src.application.orders.repository import OrderLedgerRepository

    repository = OrderLedgerRepository(trader.connect_db)
    item = repository.get(order_id)
    if not item:
        raise HTTPException(status_code=404, detail="order not found")
    broker_order_id = str(item.get("broker_order_id") or "")
    if not broker_order_id:
        raise HTTPException(status_code=409, detail="broker order id is not known")
    snapshot = _get_api().fetch_order_snapshot(
        broker_order_id, str(item.get("created_at") or "")[:10].replace("-", "")
    )
    status = str(getattr(snapshot.status, "value", snapshot.status))
    if snapshot.outcome_unknown:
        status = "broker_unknown"
    return repository.reconcile_snapshot(
        order_id,
        status=status,
        cumulative_filled_qty=int(snapshot.filled_quantity),
        average_fill_price=float(snapshot.average_fill_price),
        broker_order_id=snapshot.broker_order_id or broker_order_id,
        broker_order_date=str(item.get("broker_order_date") or ""),
        raw=dict(snapshot.raw),
    )


def _confirm_canceled_order(order_id: int, *, attempts: int = 8, interval_seconds: float = 2.0) -> None:
    """Confirm one cancellation without running the expensive account-wide history sync."""
    from src.application.orders.repository import OrderLedgerRepository

    repository = OrderLedgerRepository(trader.connect_db)
    last_message = ""
    for attempt in range(1, max(1, attempts) + 1):
        item = repository.get(order_id)
        if not item or str(item.get("status") or "") not in {"cancel_pending", "broker_unknown"}:
            return
        broker_order_id = str(item.get("broker_order_id") or "")
        order_date = str(item.get("broker_order_date") or "").replace("-", "")
        if not order_date:
            order_date = str(item.get("created_at") or "")[:10].replace("-", "")
        try:
            snapshot = _get_api().fetch_order_snapshot(broker_order_id, order_date)
            status = str(getattr(snapshot.status, "value", snapshot.status))
            if snapshot.outcome_unknown:
                last_message = snapshot.message or "order not found in broker history"
            elif status in {"canceled", "cancelled", "filled"}:
                repository.reconcile_snapshot(
                    order_id,
                    status=status,
                    cumulative_filled_qty=int(snapshot.filled_quantity),
                    average_fill_price=float(snapshot.average_fill_price),
                    broker_order_id=snapshot.broker_order_id or broker_order_id,
                    broker_order_date=str(item.get("broker_order_date") or ""),
                    raw=dict(snapshot.raw),
                )
                repository.record_event(
                    order_id,
                    "cancel_confirmed",
                    actor="broker_poll",
                    reason=f"single-order cancellation confirmation attempt={attempt}",
                    payload={"status": status, "remaining_qty": int(snapshot.remaining_quantity)},
                )
                _clear_balance_cache()
                return
            else:
                last_message = f"broker still reports {status or 'unknown'}"
        except Exception as exc:
            last_message = str(exc)
        if attempt < attempts:
            time.sleep(max(0.0, interval_seconds))

    item = repository.get(order_id)
    if item and str(item.get("status") or "") == "cancel_pending":
        repository.transition(
            order_id,
            "cancel_pending",
            "broker_unknown",
            actor="broker_poll",
            reason=f"cancellation confirmation timed out: {last_message or 'no terminal snapshot'}",
            payload={"attempts": attempts},
        )


def resume_cancel_pending_confirmations() -> int:
    """Resume bounded single-order checks after a dashboard restart."""
    from src.application.orders.repository import OrderLedgerRepository

    repository = OrderLedgerRepository(trader.connect_db)
    items = repository.list_orders(
        statuses=("cancel_pending", "broker_unknown"), limit=100, offset=0
    )
    items = [
        item for item in items
        if str(item.get("status") or "") == "cancel_pending"
        or any(
            str(event.get("to_status") or "") == "cancel_pending"
            for event in (repository.detail(int(item["id"])) or {}).get("events", [])
        )
    ]
    for item in items:
        order_id = int(item["id"])
        threading.Thread(
            target=_confirm_canceled_order,
            args=(order_id,),
            name=f"order-cancel-resume-{order_id}",
            daemon=True,
        ).start()
    return len(items)


@router.post("/api/orders/{order_id}/cancel")
def cancel_unified_order(order_id: int):
    from fastapi import HTTPException
    from src.application.orders.repository import OrderLedgerRepository
    from src.broker.models import CancelOrderRequest

    repository = OrderLedgerRepository(trader.connect_db)
    item = repository.get(order_id)
    if not item:
        raise HTTPException(status_code=404, detail="order not found")
    current = str(item["status"])
    if current not in {"submitted", "open", "partial"}:
        raise HTTPException(status_code=409, detail=f"order cannot be canceled from {current}")
    broker_order_id = str(item.get("broker_order_id") or "")
    if not broker_order_id:
        raise HTTPException(status_code=409, detail="broker order id is not known")
    claimed = repository.transition(
        order_id, current, "cancel_pending", actor="dashboard", reason="operator cancellation"
    )
    try:
        result = _get_api().submit_cancellation(CancelOrderRequest(
            order_id=broker_order_id,
            symbol=str(item["symbol"]),
            quantity=max(0, int(item["requested_qty"]) - int(item["filled_qty"])),
        ))
    except Exception as exc:
        message = str(exc)
        repository.record_event(
            order_id,
            "broker_cancel_exception",
            actor="broker",
            reason=message,
            payload={"original_broker_order_id": broker_order_id},
        )
        repository.transition(
            order_id, "cancel_pending", "broker_unknown", actor="broker", reason=message
        )
        raise HTTPException(
            status_code=409,
            detail="증권사 취소 결과를 확정할 수 없습니다. 주문 현행화를 실행해 주세요.",
        ) from exc
    repository.record_event(
        order_id,
        "broker_cancel_response",
        actor="broker",
        reason=result.message,
        payload={
            "success": bool(result.success),
            "status": str(getattr(result.status, "value", result.status)),
            "original_broker_order_id": broker_order_id,
            "cancellation_broker_order_id": result.broker_order_id,
            "raw": dict(result.raw),
        },
    )
    if not result.success:
        # Cancellation outcome may be ambiguous. Reconciliation is required;
        # never restore the prior active state and blindly retry.
        claimed = repository.transition(
            order_id, "cancel_pending", "broker_unknown", actor="broker", reason=result.message
        )
    else:
        threading.Thread(
            target=_confirm_canceled_order,
            args=(order_id,),
            name=f"order-cancel-confirm-{order_id}",
            daemon=True,
        ).start()
    return {"order": claimed, "broker_result": {
        "success": result.success, "message": result.message,
        "broker_order_id": result.broker_order_id,
        "status": str(getattr(result.status, "value", result.status)),
    }, "confirmation_started": bool(result.success)}


@router.post("/api/orders/{order_id}/resolve-unknown")
def resolve_unknown_unified_order(order_id: int, payload: dict = Body(...)):
    """Close a broker-unknown row only after explicit operator verification."""
    from src.application.orders.repository import OrderLedgerRepository

    if str(payload.get("confirmation") or "") != "BROKER_ORDER_NOT_FOUND":
        raise HTTPException(status_code=400, detail="증권사 미체결 없음 확인이 필요합니다")
    repository = OrderLedgerRepository(trader.connect_db)
    item = repository.get(order_id)
    if not item:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다")
    if str(item.get("status") or "") != "broker_unknown":
        raise HTTPException(status_code=409, detail="미확인 주문 상태에서만 종결할 수 있습니다")
    if str(item.get("broker_order_id") or "").strip():
        raise HTTPException(status_code=409, detail="증권사 주문번호가 있는 주문은 취소 또는 현행화해야 합니다")

    reason = "운영자가 증권사 미체결 주문 없음 확인 후 종결"
    managed_order_id = 0
    approval_id = int(item.get("approval_id") or 0)
    if approval_id:
        with trader.connect_db() as conn:
            row = conn.execute(
                "SELECT managed_order_id FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            managed_order_id = int((row[0] if row else 0) or 0)
    if managed_order_id:
        from src.db import ai_autonomy_repository as ai_stock_repository
        from src.strategy.autonomy.order_state import ManagedOrderService, OrderStatus

        managed = ai_stock_repository.get_managed_order(managed_order_id)
        if managed and str(managed.get("status") or "") == "broker_unknown":
            ManagedOrderService(repo=ai_stock_repository).reject(
                managed_order_id, expected=OrderStatus.BROKER_UNKNOWN, reason=reason
            )

    resolved = repository.transition(
        order_id, "broker_unknown", "rejected",
        actor="dashboard", reason=reason,
        payload={"operator_confirmation": "BROKER_ORDER_NOT_FOUND"},
    )
    if approval_id:
        now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
        with trader.connect_db() as conn:
            conn.execute(
                "UPDATE approvals SET status='rejected',response_msg=?,updated_at=? "
                "WHERE id=? AND status NOT IN ('executed','rejected','expired')",
                (reason, now, approval_id),
            )
    from src.application.orders.recovery import run_startup_recovery

    recovery = run_startup_recovery(trader.connect_db)
    return {"ok": True, "order": resolved, "recovery_state": recovery.get("state")}


@router.get("/api/operations/order-health")
def unified_order_health():
    from src.application.orders.health import build_order_health

    return build_order_health(trader.connect_db)


@router.get("/api/operations/health")
def operations_health():
    """Canonical operations endpoint retained alongside the narrower alias."""
    return unified_order_health()


@router.get("/api/reconciliation/issues")
def reconciliation_issues(status: str = "open", limit: int = 100, offset: int = 0):
    limit = min(500, max(1, int(limit)))
    offset = max(0, int(offset))
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM reconciliation_adjustments
               WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?""",
            (status, limit, offset),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "status": status, "limit": limit, "offset": offset}


@router.post("/api/reconciliation/issues/apply-broker-balance")
def apply_broker_balance_reconciliation(payload: dict = Body(...)):
    """Align position projections only after revalidating every issue against live balance."""
    from src.application.orders.position_reconciliation import (
        apply_latest_open_reconciliation_issues,
    )
    from src.application.orders.recovery import run_startup_recovery

    confirmation = str(payload.get("confirmation") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if confirmation != "APPLY_BROKER_BALANCE":
        raise HTTPException(status_code=400, detail="explicit broker balance confirmation is required")
    if not reason:
        raise HTTPException(status_code=400, detail="review reason is required")

    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        latest = conn.execute(
            """SELECT r.* FROM reconciliation_adjustments r
               JOIN (
                 SELECT account_key,market,symbol,MAX(id) AS id
                 FROM reconciliation_adjustments WHERE status='open'
                 GROUP BY account_key,market,symbol
               ) newest ON newest.id=r.id ORDER BY r.symbol"""
        ).fetchall()
    if not latest:
        recovery = run_startup_recovery(trader.connect_db)
        return {
            "status": "empty", "applied_count": 0, "items": [],
            "recovery": recovery, "health": unified_order_health(),
        }

    try:
        parsed = _parse_balance(_get_balance_data(_get_api(), allow_cache=False))
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Kiwoom live balance validation failed: {exc}"
        ) from exc
    live_quantities = {
        str(item.get("symbol") or "").strip(): _to_int(item.get("qty"))
        for item in parsed.get("holdings", [])
    }
    refreshed = []
    for row in latest:
        expected = int(row["broker_qty"])
        actual = int(live_quantities.get(str(row["symbol"]), 0))
        if expected != actual:
            issue_id = _record_reconciliation_issue(
                symbol=str(row["symbol"]),
                broker_qty=actual,
                internal_qty=int(row["internal_qty"]),
                reason="live broker balance refreshed before operator alignment",
                snapshot={
                    "supersedes_issue_id": int(row["id"]),
                    "recorded_broker_qty": expected,
                    "live_broker_qty": actual,
                },
            )
            refreshed.append({
                "issue_id": issue_id, "symbol": str(row["symbol"]),
                "recorded_broker_qty": expected, "live_broker_qty": actual,
            })

    actor = f"dashboard:{reason[:120]}"
    applied = apply_latest_open_reconciliation_issues(trader.connect_db, actor=actor)
    recovery = run_startup_recovery(trader.connect_db)
    _clear_balance_cache()
    return {
        "status": "applied",
        **applied,
        "refreshed_count": len(refreshed),
        "refreshed_items": refreshed,
        "recovery": recovery,
        "health": unified_order_health(),
    }


@router.post("/api/reconciliation/issues/{issue_id}/review")
def review_reconciliation_issue(issue_id: int, payload: dict = Body(...)):
    status = str(payload.get("status") or "").strip().lower()
    reviewer = str(payload.get("reviewed_by") or "dashboard").strip()
    reason = str(payload.get("reason") or "").strip()
    if status not in {"resolved", "ignored"}:
        raise HTTPException(status_code=400, detail="status must be resolved or ignored")
    if not reason:
        raise HTTPException(status_code=400, detail="review reason is required")
    now_value = trader.datetime.now(trader.KST)
    now = now_value.strftime("%Y-%m-%d %H:%M:%S")
    with trader.connect_db() as conn:
        cursor = conn.execute(
            """UPDATE reconciliation_adjustments
               SET status=?, reviewed_by=?, reviewed_at=?, reason=reason || ' | review: ' || ?
               WHERE id=? AND status='open'""",
            (status, reviewer, now, reason, issue_id),
        )
    if cursor.rowcount != 1:
        raise HTTPException(status_code=409, detail="issue is missing or already reviewed")
    return {"id": issue_id, "status": status, "reviewed_by": reviewer, "reviewed_at": now}


@router.post("/api/orders/{order_id}/approve")
def approve_unified_order(order_id: int):
    from fastapi import HTTPException
    from src.application.orders.repository import OrderLedgerRepository

    item = OrderLedgerRepository(trader.connect_db).get(order_id)
    if not item:
        raise HTTPException(status_code=404, detail="order not found")
    approval_id = _to_int(item.get("approval_id"))
    if approval_id <= 0:
        raise HTTPException(status_code=409, detail="order has no approval to execute")
    return _approve_pending_approval(approval_id, "통합원장 승인")


def _balance_sync_strategy_id(trade: dict) -> str:
    from src.strategy_ids import resolve_order_strategy_id

    return resolve_order_strategy_id(
        trade.get("strategy_id"),
        reason=str(trade.get("reason") or ""),
    )


def _strategy_position_quantities(trades: list[dict]) -> dict[str, dict[str, int]]:
    """Return positive, recorded strategy quantities by symbol."""
    positions: dict[str, dict[str, int]] = {}
    position_trades = _account_trades(trades)
    position_trades.extend(
        trade for trade in trades
        if _trade_is_ok(trade)
        and str(trade.get("reason") or "").strip() == _ATTRIBUTED_BALANCE_SYNC_REASON
    )
    for trade in position_trades:
        symbol = str(trade.get("symbol") or "").strip()
        strategy_id = _balance_sync_strategy_id(trade)
        action = str(trade.get("action") or "").strip().lower()
        if not symbol or not strategy_id or action not in {"buy", "sell"}:
            continue
        qty = _to_int(trade.get("qty"))
        if qty <= 0:
            continue
        by_strategy = positions.setdefault(symbol, {})
        delta = qty if action == "buy" else -qty
        by_strategy[strategy_id] = by_strategy.get(strategy_id, 0) + delta
    return {
        symbol: {strategy_id: qty for strategy_id, qty in by_strategy.items() if qty > 0}
        for symbol, by_strategy in positions.items()
    }


def _allocate_strategy_reconciliation(
    qty: int,
    strategy_quantities: dict[str, int],
    *,
    action: str,
) -> list[tuple[str | None, int]]:
    """Allocate a broker balance adjustment without losing known ownership."""
    qty = max(0, int(qty))
    owners = sorted(
        ((str(strategy_id), int(owner_qty)) for strategy_id, owner_qty in strategy_quantities.items()
         if str(strategy_id).strip() and int(owner_qty) > 0),
        key=lambda item: item[0],
    )
    if qty <= 0:
        return []
    if not owners:
        if action == "buy":
            from src.strategy_ids import BROKER_BASELINE_STRATEGY_ID

            return [(BROKER_BASELINE_STRATEGY_ID, qty)]
        return [(None, qty)]

    allocatable = min(qty, sum(owner_qty for _, owner_qty in owners)) if action == "sell" else qty
    total_weight = sum(owner_qty for _, owner_qty in owners)
    allocations = []
    assigned = 0
    remainders = []
    for strategy_id, owner_qty in owners:
        numerator = allocatable * owner_qty
        allocated = numerator // total_weight
        if action == "sell":
            allocated = min(allocated, owner_qty)
        allocations.append([strategy_id, allocated])
        assigned += allocated
        remainders.append((numerator % total_weight, strategy_id, owner_qty))

    remaining = allocatable - assigned
    for _, strategy_id, owner_qty in sorted(remainders, key=lambda item: (-item[0], item[1])):
        if remaining <= 0:
            break
        allocation = next(item for item in allocations if item[0] == strategy_id)
        if action != "sell" or allocation[1] < owner_qty:
            allocation[1] += 1
            remaining -= 1

    result = [(strategy_id, allocated) for strategy_id, allocated in allocations if allocated > 0]
    unattributed = qty - allocatable
    if unattributed > 0:
        result.append((None, unattributed))
    return result

@router.get("/api/approvals")
def get_approvals(limit: int = 50, strategy_id: str | None = None):
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be greater than 0")
    limit = min(limit, 200)
    auto_approval_enabled = _auto_approval_enabled()
    _reclaim_stale_executing_approvals()

    _init_approval_db()
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        if strategy_id:
            recent_rows = conn.execute(
                "SELECT * FROM approvals WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
                (strategy_id, limit),
            ).fetchall()
            actionable_rows = conn.execute(
                """
                SELECT * FROM approvals
                WHERE strategy_id = ? AND status IN ('pending', 'executing', 'failed', 'broker_unknown')
                ORDER BY id DESC
                """,
                (strategy_id,),
            ).fetchall()
        else:
            recent_rows = conn.execute(
                "SELECT * FROM approvals ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            actionable_rows = conn.execute(
                """
                SELECT * FROM approvals
                WHERE status IN ('pending', 'executing', 'failed', 'broker_unknown')
                ORDER BY id DESC
                """
            ).fetchall()
        rows_by_id = {int(row["id"]): row for row in recent_rows}
        rows_by_id.update({int(row["id"]): row for row in actionable_rows})
        rows = sorted(
            rows_by_id.values(), key=lambda row: int(row["id"]), reverse=True
        )[:limit]
    try:
        from src.db.repository import load_ai_strategies

        strategy_names = {
            str(strategy.get("id") or ""): _strategy_display_name(
                strategy.get("id"),
                strategy.get("name"),
            )
            for strategy in load_ai_strategies()
            if strategy.get("id")
        }
    except Exception:
        strategy_names = {}

    approvals = []
    approval_ids = [int(row["id"]) for row in rows]
    latest_trades = _latest_trades_by_approval_ids(approval_ids)
    unified_by_approval = {}
    if approval_ids:
        with trader.connect_db() as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in approval_ids)
            unified_rows = conn.execute(
                f"SELECT * FROM orders WHERE approval_id IN ({placeholders}) ORDER BY id DESC",
                tuple(approval_ids),
            ).fetchall()
            for unified_row in unified_rows:
                unified_by_approval.setdefault(int(unified_row["approval_id"]), dict(unified_row))
    open_sells = _latest_open_sell_trades_by_symbols([str(row["symbol"]) for row in rows])
    for row in rows:
        item = _approval_row(row)
        item["price"] = _to_int(item.get("price"))
        strategy_id_value = str(item.get("strategy_id") or "").strip()
        strategy_name_value = (

            strategy_names.get(strategy_id_value)
            if strategy_id_value
            else None
        )
        if strategy_name_value:
            item["strategy_name"] = strategy_name_value
        item.update(_approval_classification(
            strategy_id=strategy_id_value,
            strategy_name=strategy_name_value,
            source=item.get("source"),
        ))
        trade = latest_trades.get(int(item.get("id") or 0))
        unified_order = unified_by_approval.get(int(item.get("id") or 0))
        if unified_order:
            item["internal_order_id"] = unified_order.get("id")
            item["correlation_id"] = unified_order.get("correlation_id")
            item["unified_order_status"] = unified_order.get("status")
            item["last_synced_at"] = unified_order.get("last_synced_at")
            item["expires_at"] = item.get("expires_at") or unified_order.get("expires_at")
        if trade:
            item["submission_message"] = str(item.get("response_msg") or "")
            item["trade_id"] = trade.get("id")
            item["broker_order_id"] = trade.get("broker_order_id")
            item["order_status"] = trade.get("order_status")
            item["filled_qty"] = _to_int(trade.get("filled_qty"))
            item["filled_price"] = _to_int(trade.get("filled_price"))
            item["pre_order_qty"] = _to_int(trade.get("pre_order_qty"))
            item["retry_eligible"] = _approval_retry_eligible(item, trade)
        else:
            item["filled_qty"] = 0
            item["filled_price"] = 0
            item["retry_eligible"] = _approval_retry_eligible(item, None)
        item["result_message"] = _approval_result_message(item, trade)
        item["remaining_qty"] = max(
            0, _to_int(item.get("qty")) - _to_int(item.get("filled_qty"))
        )
        item["stale"] = bool(
            str(item.get("created_at") or "")[:10]
            and str(item.get("created_at") or "")[:10]
            != trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
        )
        blocking_sell = open_sells.get(str(item.get("symbol") or "").strip())
        if blocking_sell:
            item["blocking_order_id"] = blocking_sell.get("broker_order_id")
            item["blocking_remaining_qty"] = max(
                0,
                _to_int(blocking_sell.get("qty")) - _to_int(blocking_sell.get("filled_qty")),
            )
        has_blocking_order = _to_int(item.get("blocking_remaining_qty")) > 0
        item["direct_retry_eligible"] = bool(item["retry_eligible"] and not has_blocking_order)
        item["cancel_retry_eligible"] = bool(item["retry_eligible"] and has_blocking_order)
        item["auto_approval_in_progress"] = (
            auto_approval_enabled
            and item.get("status") == "pending"
            and item.get("source") in {
                "dashboard_sell_all",
                "dashboard_holding_sell",
                "dashboard_strategy_holding_sell",
                "dashboard_strategy_sell_all",
            }
        )
        approvals.append(item)
    return {
        "approvals": approvals,
        "verification_required_count": sum(
            1 for item in approvals
            if item.get("status") == "broker_unknown"
            or item.get("order_status") == "broker_unknown"
        ),
        "actionable_count": sum(
            1
            for item in approvals
            if (
                item.get("status") in {"pending", "executing"}
                and not bool(item.get("stale"))
            )
            or bool(item.get("retry_eligible"))
            or item.get("status") == "broker_unknown"
            or item.get("order_status") == "broker_unknown"
        ),
        "recent_limit": limit,
    }


def _latest_open_sell_trades_by_symbols(symbols: list[str]) -> dict[str, dict]:
    normalized = sorted({str(symbol or "").strip() for symbol in symbols if str(symbol or "").strip()})
    if not normalized:
        return {}
    trader.init_db()
    placeholders = ", ".join("?" for _ in normalized)
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT *
            FROM trades
            WHERE symbol IN ({placeholders})
              AND action = 'sell'
              AND order_status IN (
                  'submitted', 'accepted', 'open', 'partial', 'partially_filled'
              )
              AND COALESCE(broker_order_id, '') != ''
              AND qty > COALESCE(filled_qty, 0)
            ORDER BY id DESC
            """,

            normalized,
        ).fetchall()
    latest: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        symbol = str(item.get("symbol") or "").strip()
        if symbol and symbol not in latest:
            latest[symbol] = item
    return latest


def _latest_trades_by_approval_ids(approval_ids: list[int]) -> dict[int, dict]:
    ids = [int(approval_id) for approval_id in approval_ids if int(approval_id or 0) > 0]
    if not ids:
        return {}
    trader.init_db()
    placeholders = ", ".join("?" for _ in ids)
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT *
            FROM trades
            WHERE source_approval_id IN ({placeholders})
            ORDER BY id DESC
            """,
            ids,
        ).fetchall()
    latest: dict[int, dict] = {}
    for row in rows:
        item = dict(row)
        approval_id = _to_int(item.get("source_approval_id"))
        if approval_id > 0 and approval_id not in latest:
            latest[approval_id] = item
    return latest


def _latest_trade_by_approval_id(approval_id: int) -> dict | None:
    return _latest_trades_by_approval_ids([approval_id]).get(int(approval_id))


def _approval_result_message(item: dict, trade: dict | None) -> str:
    """Describe the latest broker outcome, not the earlier submission response."""
    if not trade:
        return str(item.get("response_msg") or "").strip()

    status = str(trade.get("order_status") or "").strip().lower()
    requested_qty = _to_int(item.get("qty"))
    filled_qty = _to_int(trade.get("filled_qty"))
    remaining_qty = max(0, requested_qty - filled_qty)
    filled_price = _to_int(trade.get("filled_price"))
    price_text = f" · 평균 {filled_price:,}원" if filled_price > 0 else ""

    if status in {"filled", "reconciled"}:
        return f"전량 체결 · {filled_qty:,}/{requested_qty:,}주{price_text}"
    if status in {"partial", "partially_filled"}:
        return (
            f"부분 체결 · {filled_qty:,}/{requested_qty:,}주"
            f" · 잔여 {remaining_qty:,}주{price_text}"
        )
    if status in {"submitted", "open", "accepted"}:
        return f"주문 접수 · 체결 {filled_qty:,}주 · 잔여 {remaining_qty:,}주"
    if status in {"cancelled", "canceled"}:
        return f"주문 취소 · 체결 {filled_qty:,}주 · 취소 잔량 {remaining_qty:,}주"
    if status == "rejected":
        return "증권사 주문 거부"
    if status == "broker_unknown":
        return "증권사 확인 필요 · 체결 여부 미확정"
    if status == "failed":
        return str(trade.get("response_msg") or item.get("response_msg") or "주문 실패").strip()
    return str(trade.get("response_msg") or item.get("response_msg") or status).strip()


def _approval_retry_eligible(item: dict, trade: dict | None) -> bool:
    if str(item.get("action") or "").lower() != "sell":
        return False
    created_at = str(item.get("created_at") or "").strip()
    if created_at:
        today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
        if created_at[:10] != today:
            return False
    if str(item.get("status") or "") == "pending":
        return False
    trade_status = str((trade or {}).get("order_status") or "").lower()
    approval_status = str(item.get("status") or "").lower()
    if trade_status in {
        "failed", "submitted", "accepted", "open", "partial", "partially_filled"
    }:
        return True
    return approval_status == "failed"


def _current_sellable_qty(symbol: str) -> int:
    try:
        parsed = _parse_balance(_get_balance_data(_get_api(), allow_cache=False))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kiwoom balance API request failed: {exc}") from exc
    for holding in parsed.get("holdings", []):
        if str(holding.get("symbol") or "").strip() == str(symbol).strip():

            holding_qty = _to_int(holding.get("qty"))
            sellable_qty = _to_int(holding.get("sellable_qty", holding_qty))
            return max(0, min(holding_qty, sellable_qty))
    return 0


def _open_sell_order_from_history(api, symbol: str) -> dict | None:
    start_date, end_date = _order_history_window(MIN_ORDER_HISTORY_SYNC_DAYS)
    try:
        history = api.get_trade_history(start_date, end_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kiwoom order history request failed: {exc}") from exc
    candidates = []
    for row in history:
        if _history_symbol(row) != str(symbol).strip():
            continue
        if _history_action(row) != "sell":
            continue
        remaining_qty = _history_remaining_qty(row)
        if remaining_qty <= 0:
            continue
        canceled = _history_text(row, "cncl_yn", "CNCL_YN", "canceled", "cancel_yn").upper()
        if canceled in {"Y", "취소"}:
            continue
        candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda row: (_history_timestamp(row), _broker_order_id_from_history(row)), reverse=True)
    return candidates[0]


def _create_retry_approval_from_item(item: dict, *, retry_qty: int, source_approval_id: int) -> int:
    symbol = str(item.get("symbol") or "").strip()
    return _create_approval_row({
        "symbol": symbol,
        "name": item.get("name") or symbol,
        "action": "sell",
        "qty": retry_qty,
        "price": 0,
        "reason": f"retry approval #{source_approval_id}: {item.get('reason') or ''}".strip(),
        "source": "dashboard_retry",
        "strategy_id": item.get("strategy_id"),
        "strategy_version": item.get("strategy_version"),
        "profile_hash": item.get("profile_hash"),
        "source_candidate_id": item.get("source_candidate_id"),
    })


def _save_approval_batch_state(state: dict) -> None:
    APPROVAL_BATCH_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = APPROVAL_BATCH_RESULT_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(APPROVAL_BATCH_RESULT_PATH)


def _run_approval_batch(job_id: str, action: str, approval_ids: list[int]) -> None:
    results = []
    success_count = 0
    failed_count = 0
    for index, approval_id in enumerate(approval_ids, start=1):

        try:
            if action == "cancel-retry":
                result = cancel_blocking_sell_and_retry_approval(approval_id)
            else:
                result = retry_approval_order(approval_id)
            success_count += 1
            results.append({"approval_id": approval_id, "ok": True, "result": result})
        except Exception as exc:
            failed_count += 1
            detail = getattr(exc, "detail", None) or str(exc)
            results.append({"approval_id": approval_id, "ok": False, "error": str(detail)})

        with _approval_batch_lock:
            if _approval_batch_state.get("job_id") != job_id:
                return
            _approval_batch_state.update({
                "processed_count": index,
                "success_count": success_count,
                "failed_count": failed_count,
                "results": results,
            })
            _save_approval_batch_state(_approval_batch_state)

    with _approval_batch_lock:
        if _approval_batch_state.get("job_id") != job_id:
            return
        _approval_batch_state.update({
            "status": "completed",
            "completed_at": trader.datetime.now(trader.KST).isoformat(),
        })
        _save_approval_batch_state(_approval_batch_state)


@router.post("/api/approvals/batch")
def start_approval_batch(payload: dict = Body(...)):
    action = str(payload.get("action") or "").strip()
    if action not in {"retry", "cancel-retry"}:
        raise HTTPException(status_code=400, detail="action must be retry or cancel-retry")
    approval_ids = []
    for value in payload.get("approval_ids") or []:
        approval_id = _to_int(value)
        if approval_id > 0 and approval_id not in approval_ids:
            approval_ids.append(approval_id)
    if not approval_ids:
        raise HTTPException(status_code=400, detail="approval_ids is required")
    if len(approval_ids) > 50:
        raise HTTPException(status_code=400, detail="approval_ids must contain at most 50 items")

    with _approval_batch_lock:
        if _approval_batch_state.get("status") == "running":
            raise HTTPException(status_code=409, detail="another approval batch is already running")
        job_id = trader.datetime.now(trader.KST).strftime("%Y%m%d%H%M%S%f")
        _approval_batch_state.clear()
        _approval_batch_state.update({
            "job_id": job_id,
            "action": action,
            "status": "running",
            "total_count": len(approval_ids),
            "processed_count": 0,
            "success_count": 0,

            "failed_count": 0,
            "results": [],
            "started_at": trader.datetime.now(trader.KST).isoformat(),
            "completed_at": None,
        })
        _save_approval_batch_state(_approval_batch_state)

    threading.Thread(
        target=_run_approval_batch,
        args=(job_id, action, approval_ids),
        name=f"approval-batch-{job_id}",
        daemon=True,
    ).start()
    return dict(_approval_batch_state)


@router.get("/api/approvals/batch/status")
def get_approval_batch_status(job_id: str = ""):
    with _approval_batch_lock:
        if _approval_batch_state:
            current = dict(_approval_batch_state)
            if job_id and str(current.get("job_id") or "") != job_id:
                raise HTTPException(status_code=409, detail="approval batch job was replaced")
            return {"available": True, **current}
    if APPROVAL_BATCH_RESULT_PATH.exists():
        try:
            saved = json.loads(APPROVAL_BATCH_RESULT_PATH.read_text(encoding="utf-8"))
            if job_id and str(saved.get("job_id") or "") != job_id:
                raise HTTPException(status_code=409, detail="approval batch job was replaced")
            if saved.get("status") == "running":
                saved.update({
                    "status": "interrupted",
                    "completed_at": trader.datetime.now(trader.KST).isoformat(),
                    "error": "서버 재기동으로 일괄 작업이 중단되었습니다. 주문 현황을 확인한 뒤 다시 실행해 주세요.",
                })
                _save_approval_batch_state(saved)
            return {"available": True, **saved}
        except HTTPException:
            raise
        except (OSError, ValueError, TypeError):
            pass
    return {"available": False, "status": "idle"}


@router.post("/api/approvals/{approval_id}/retry")
def retry_approval_order(approval_id: int):
    item = _approval_by_id(approval_id)
    if not item:
        raise HTTPException(status_code=404, detail="approval not found")
    trade = _latest_trade_by_approval_id(approval_id)
    if not _approval_retry_eligible(item, trade):
        raise HTTPException(status_code=409, detail="approval is not retryable")

    symbol = str(item.get("symbol") or "").strip()
    trade_status = str((trade or {}).get("order_status") or "").lower()
    blocking_order = None
    if trade_status in {"submitted", "accepted", "open", "partial", "partially_filled"}:
        blocking_order = trade
    else:
        api = _get_api()
        if hasattr(api, "get_trade_history"):
            blocking_order = _open_sell_order_from_history(api, symbol)
    blocking_remaining_qty = (
        max(0, _to_int(blocking_order.get("qty")) - _to_int(blocking_order.get("filled_qty")))
        if blocking_order is trade
        else _history_remaining_qty(blocking_order) if blocking_order else 0
    )
    if blocking_order and blocking_remaining_qty > 0:
        order_no = (
            str(blocking_order.get("broker_order_id") or "")
            if blocking_order is trade
            else _broker_order_id_from_history(blocking_order)
        ) or "-"
        raise HTTPException(
            status_code=409,
            detail=(
                "an open broker sell order still exists "
                f"(order {order_no}); use cancel-retry to prevent a duplicate sell"
            ),
        )
    sellable_qty = _current_sellable_qty(symbol)
    if sellable_qty <= 0:
        raise HTTPException(
            status_code=409,
            detail="sellable quantity is zero; cancel or wait for the existing sell order to settle before retry",
        )

    original_qty = _to_int(item.get("qty"))
    filled_qty = _to_int((trade or {}).get("filled_qty"))
    remaining_qty = max(0, original_qty - filled_qty)
    retry_qty = min(sellable_qty, remaining_qty if remaining_qty > 0 else sellable_qty)
    if retry_qty <= 0:
        raise HTTPException(status_code=409, detail="no remaining quantity to retry")

    retry_id = _create_retry_approval_from_item(item, retry_qty=retry_qty, source_approval_id=approval_id)
    return {
        "id": retry_id,
        "status": "pending",
        "source_approval_id": approval_id,
        "symbol": symbol,

        "qty": retry_qty,
        "sellable_qty": sellable_qty,
    }


@router.post("/api/approvals/{approval_id}/cancel-retry")
def cancel_blocking_sell_and_retry_approval(approval_id: int):
    item = _approval_by_id(approval_id)
    if not item:
        raise HTTPException(status_code=404, detail="approval not found")
    trade = _latest_trade_by_approval_id(approval_id)
    if not _approval_retry_eligible(item, trade):
        raise HTTPException(status_code=409, detail="approval is not retryable")

    symbol = str(item.get("symbol") or "").strip()
    api = _get_api()
    blocking_order = _open_sell_order_from_history(api, symbol)
    if not blocking_order:
        return retry_approval_order(approval_id)

    order_no = _broker_order_id_from_history(blocking_order)
    remaining_qty = _history_remaining_qty(blocking_order)
    branch = _history_text(blocking_order, "ord_gno_brno", "ORD_GNO_BRNO")
    if not order_no or remaining_qty <= 0:
        raise HTTPException(status_code=409, detail="blocking sell order was not identifiable")

    try:
        cancel_result = api.cancel_order(
            order_no,
            symbol=symbol,
            qty=remaining_qty,
            original_order_branch=branch,
            cancel_all=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kiwoom order cancellation failed: {exc}") from exc
    if str(cancel_result.get("rt_cd") or "") != "0":
        raise HTTPException(
            status_code=409,
            detail=f"Kiwoom order cancellation rejected: {cancel_result.get('msg1') or cancel_result}",
        )

    trader.update_trade_order_status(
        order_no,
        order_status="canceled",
        filled_qty=_history_fill_qty(blocking_order),
        filled_price=_history_fill_price(blocking_order),
        response_msg="Kiwoom blocking sell order canceled before retry",
        broker_result=cancel_result,
    )
    _clear_balance_cache()

    # The canceled broker order owns the currently locked quantity. Recreate its
    # entire remainder instead of capping it to an older failed approval amount.
    retry_qty = remaining_qty
    retry_id = _create_retry_approval_from_item(item, retry_qty=retry_qty, source_approval_id=approval_id)
    return {
        "id": retry_id,
        "status": "pending",
        "source_approval_id": approval_id,
        "symbol": symbol,

        "qty": retry_qty,
        "canceled_order_id": order_no,
        "cancel_result": cancel_result,
    }




@router.post("/api/approvals")
def create_approval(payload: dict = Body(...)):
    source = str(payload.get("source") or "")
    if source == "dashboard_holding_sell":
        symbol = str(payload.get("symbol") or "").strip()
        with _holding_sell_request_lock:
            if symbol and symbol in _active_dashboard_sell_symbols():
                raise HTTPException(
                    status_code=409,
                    detail=f"active sell request already exists for {symbol}",
                )
            approval_id = _create_approval_row(payload)
    else:
        approval_id = _create_approval_row(payload)
    if _auto_approval_enabled():
        if source == "dashboard_holding_sell":
            _run_auto_approval_batch_async([approval_id])
            return {
                "id": approval_id,
                "status": "pending",
                "auto_approved": False,
                "auto_approval_queued": True,
            }
        result = _approve_pending_approval(approval_id, "auto approval")
        result["auto_approved"] = True
        return result
    return {"id": approval_id, "status": "pending"}


@router.post("/api/strategy-lookup/manual-buy")
def create_strategy_lookup_manual_buy(payload: dict = Body(...)):
    """Queue an operator-requested buy even when analysis excluded the symbol.

    This endpoint intentionally never auto-approves. The ordinary approval
    execution path remains responsible for account, duplicate-order, and kill
    switch checks.
    """
    if payload.get("manual_override_acknowledged") is not True:
        raise HTTPException(
            status_code=400,
            detail="manual_override_acknowledged=true is required",
        )
    qty = _to_int(payload.get("qty"))
    price = _to_int(payload.get("price"))
    if qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be greater than 0")
    if price <= 0:
        raise HTTPException(status_code=400, detail="price must be greater than 0")

    symbol = str(payload.get("symbol") or "").strip()
    verdict = str(payload.get("analysis_verdict") or "").strip() or "unknown"
    strategy_id = str(payload.get("strategy_id") or "").strip()
    reason_detail = str(payload.get("reason") or "").strip()
    reason = (
        f"Strategy lookup manual override (verdict={verdict})"
        + (f": {reason_detail}" if reason_detail else "")
    )
    approval_id = _create_approval_row({
        "symbol": symbol,
        "name": str(payload.get("name") or symbol),
        "action": "buy",
        "qty": qty,
        "price": price,
        "reason": reason,
        "source": "strategy_lookup_manual",
        "strategy_id": strategy_id or "manual_strategy",
        "strategy_version": payload.get("strategy_version"),
        "profile_hash": payload.get("profile_hash"),
    })
    logger.warning(
        "[MANUAL_BUY_OVERRIDE] approval_id={} symbol={} qty={} price={} "
        "strategy_id={} analysis_verdict={}",
        approval_id,
        symbol,
        qty,
        price,
        strategy_id or "manual_strategy",
        verdict,
    )
    return {
        "id": approval_id,
        "status": "pending",
        "auto_approved": False,
        "manual_override": True,
    }


def _create_approval_row(payload: dict) -> int:
    action = str(payload.get("action", "")).lower()
    if action not in {"buy", "sell"}:
        raise HTTPException(status_code=400, detail="action must be buy or sell")

    symbol = str(payload.get("symbol", "")).strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    qty = _to_int(payload.get("qty"))
    if qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be greater than 0")

    price = _to_int(payload.get("price"))
    name = str(payload.get("name") or symbol)
    reason = str(payload.get("reason") or "")
    source = str(payload.get("source") or "dashboard")
    from src.strategy_ids import resolve_order_strategy_id

    strategy_id = resolve_order_strategy_id(
        payload.get("strategy_id"),
        source=source,
        reason=reason,
        category=str(payload.get("category") or ""),
    ) or None
    strategy_version = _to_int(payload.get("strategy_version")) or None
    profile_hash = str(payload.get("profile_hash") or "").strip() or None
    source_candidate_id = _to_int(payload.get("source_candidate_id")) or None
    client_order_key = str(
        payload.get("client_order_key") or payload.get("idempotency_key") or ""
    ).strip() or None
    now_value = trader.datetime.now(trader.KST)
    expires_at = str(payload.get("expires_at") or "").strip()
    import uuid

    correlation_id = str(payload.get("correlation_id") or uuid.uuid4())
    from src.application.orders.approval import create_domestic_approval

    return create_domestic_approval(
        connect=trader.connect_db,
        init_db=trader.init_db,
        symbol=symbol, name=name, action=action, qty=qty, price=price,
        reason=reason, source=source, strategy_id=strategy_id,
        strategy_version=strategy_version, profile_hash=profile_hash,
        source_candidate_id=source_candidate_id,
        client_order_key=client_order_key, correlation_id=correlation_id,
        expires_at=expires_at or None, now=now_value,
    )


def _run_auto_approval_batch_async(approval_ids: list[int]) -> None:
    def worker() -> None:
        for approval_id in approval_ids:
            try:
                _approve_pending_approval(approval_id, "auto approval")
            except Exception as exc:
                detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                if "approval is already" in detail:
                    logger.debug(
                        f"sell-all auto approval skipped approval_id={approval_id}: {exc}"
                    )
                    continue
                logger.warning(f"sell-all auto approval failed approval_id={approval_id}: {exc}")

    import threading

    thread = threading.Thread(target=worker, name="sell-all-auto-approval", daemon=False)
    thread.start()


def _active_dashboard_sell_symbols() -> set[str]:
    trader.init_db()
    _init_approval_db()
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT DISTINCT a.symbol
            FROM approvals a
            WHERE a.action = 'sell'
              AND a.source IN (
                  'dashboard_holding_sell', 'dashboard_sell_all',
                  'dashboard_strategy_holding_sell', 'dashboard_strategy_sell_all'
              )
              AND COALESCE(a.symbol, '') <> ''
              AND (
                    a.status IN ('pending', 'executing')
                    OR (
                        a.status = 'executed'
                        AND EXISTS (
                            SELECT 1
                            FROM trades t
                            WHERE t.source_approval_id = a.id
                              AND t.id = (
                                  SELECT MAX(t2.id)
                                  FROM trades t2
                                  WHERE t2.source_approval_id = a.id
                              )
                              AND t.action = 'sell'

                              AND t.order_status IN ('submitted', 'open', 'partial')
                              AND t.qty > COALESCE(t.filled_qty, 0)
                        )
                    )
              )
            """
        ).fetchall()
    return {str(row["symbol"]) for row in rows}


def _unsubmitted_dashboard_sell_symbols() -> set[str]:
    _init_approval_db()
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT DISTINCT symbol
            FROM approvals
            WHERE action = 'sell'
              AND source IN (
                  'dashboard_holding_sell', 'dashboard_sell_all',
                  'dashboard_strategy_holding_sell', 'dashboard_strategy_sell_all'
              )
              AND status IN ('pending', 'executing')
              AND COALESCE(symbol, '') <> ''
            """
        ).fetchall()
    return {str(row["symbol"]) for row in rows}


def _cancel_open_buy_orders_before_liquidation(api) -> list[dict]:
    trader.init_db()
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE action = 'buy'
              AND order_status IN ('submitted', 'open', 'partial')
              AND COALESCE(broker_order_id, '') <> ''
              AND qty > COALESCE(filled_qty, 0)
            ORDER BY id ASC
            """
        ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        order_no = str(item.get("broker_order_id") or "")
        remaining_qty = max(
            0,
            _to_int(item.get("qty")) - _to_int(item.get("filled_qty")),
        )
        if remaining_qty <= 0:
            continue
        try:
            result = api.cancel_order(
                order_no,
                symbol=str(item.get("symbol") or ""),
                qty=remaining_qty,
                cancel_all=True,
            )
        except Exception as exc:
            message = str(exc)
            # Kiwoom demo can return RC4032 when a locally-open order already
            # became terminal at the broker.  That stale local state must not
            # prevent emergency liquidation of the current holdings.
            if "RC4032" in message or "원주문번호가 존재하지 않습니다" in message:
                results.append({
                    "broker_order_id": order_no,
                    "remaining_qty": remaining_qty,
                    "status": "already_terminal",
                    "message": message,
                })
                continue
            # Liquidation must continue for the currently sellable balance even
            # if one stale/open buy cannot be canceled because of a transient
            # broker error.  The kill switch remains active and the failure is
            # returned to operators for a later retry.
            results.append({
                "broker_order_id": order_no,
                "remaining_qty": remaining_qty,
                "status": "cancel_failed",
                "message": message,
            })
            continue
        ok = str(result.get("rt_cd") or "") == "0"
        message = str(result.get("msg1") or "")
        no_cancelable_qty = "취소할 수량이 없습니다" in message
        if not ok and not no_cancelable_qty:
            raise HTTPException(
                status_code=409,
                detail=f"open buy cancellation rejected for {order_no}: {message or result}",
            )
        if ok:
            trader.update_trade_order_status(
                order_no,
                trade_id=_to_int(item.get("id")) or None,
                order_status="canceled",
                filled_qty=_to_int(item.get("filled_qty")),
                filled_price=_to_int(item.get("filled_price")),
                response_msg="Canceled open buy before dashboard liquidation",
                broker_result=result,
            )
        results.append({
            "broker_order_id": order_no,
            "remaining_qty": remaining_qty,
            "status": "canceled" if ok else "already_terminal",
            "message": message,
        })
    return results




@router.post("/api/holdings/sell-all")
def sell_all_holdings(payload: dict | None = Body(default=None)):
    missing = _required_env_missing()
    if missing:
        raise HTTPException(status_code=503, detail=f"Missing environment variables: {', '.join(missing)}")

    halt_new_buys = bool((payload or {}).get("halt_new_buys"))
    if halt_new_buys:
        activate_kill_switch()

    try:
        api = _get_api()
        canceled_buy_orders = (
            _cancel_open_buy_orders_before_liquidation(api)
            if halt_new_buys
            else []
        )
        parsed = _parse_balance(_get_balance_data(api, allow_cache=False))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Kiwoom balance API request failed: {e}") from e

    orders = []
    skipped = []
    with _holding_sell_request_lock:
        # Submitted broker orders already reserve their quantity at Kiwoom and
        # therefore reduce sellable_qty. Only approvals not yet submitted need
        # a symbol-level duplicate guard here; otherwise newly filled buys could

        # never be liquidated while an older sell remains open.
        active_symbols = _unsubmitted_dashboard_sell_symbols()
        for holding in parsed.get("holdings", []):
            symbol = str(holding.get("symbol", "")).strip()
            holding_qty = _to_int(holding.get("qty"))
            sellable_qty = _to_int(holding.get("sellable_qty", holding_qty))
            qty = min(holding_qty, sellable_qty) if holding_qty > 0 else 0
            if not symbol:
                continue
            if symbol in active_symbols:
                skipped.append({
                    "symbol": symbol,
                    "name": str(holding.get("name") or symbol),
                    "qty": holding_qty,
                    "sellable_qty": sellable_qty,
                    "reason": "active sell request already exists",
                })
                continue
            if qty <= 0:
                skipped.append({
                    "symbol": symbol,
                    "name": str(holding.get("name") or symbol),
                    "qty": holding_qty,
                    "sellable_qty": sellable_qty,
                    "reason": "sellable quantity is zero",
                })
                continue
            orders.append({
                "symbol": symbol,
                "name": str(holding.get("name") or symbol),
                "action": "sell",
                "qty": qty,
                "price": 0,
                "reason": "dashboard sell all holdings",
                "source": "dashboard_sell_all",
            })

        approval_ids = [_create_approval_row(order) for order in orders]

    if not approval_ids:
        return {
            "status": "empty",
            "created_count": 0,
            "new_buys_halted": halt_new_buys,
            "canceled_buy_orders": canceled_buy_orders,
            "skipped_count": len(skipped),
            "skipped": skipped,
            "orders": [],
        }

    created = [{"id": approval_id, "status": "pending"} for approval_id in approval_ids]
    auto_approval_queued = False
    if _auto_approval_enabled():
        _run_auto_approval_batch_async(approval_ids)
        auto_approval_queued = True
    _clear_balance_cache()

    return {
        "status": "created",
        "created_count": len(created),

        "pending_count": sum(1 for item in created if isinstance(item, dict) and item.get("status") == "pending"),
        "submitted_count": sum(1 for item in created if isinstance(item, dict) and item.get("status") == "executed"),
        "executed_count": sum(1 for item in created if isinstance(item, dict) and item.get("status") == "executed"),
        "failed_count": sum(1 for item in created if isinstance(item, dict) and item.get("status") == "failed"),
        "auto_approved": False,
        "auto_approval_queued": auto_approval_queued,
        "new_buys_halted": halt_new_buys,
        "canceled_buy_orders": canceled_buy_orders,
        "fill_status_note": "키움 주문 접수 결과입니다. 실제 체결 여부는 주문내역 동기화 후 확정됩니다.",
        "skipped_count": len(skipped),
        "skipped": skipped,
        "orders": created,
    }


def _strategy_attribution_sell_orders(
    strategy_id: str,
    *,
    symbol: str | None = None,
    parsed_balance: dict | None = None,
    active_symbols: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    strategy_id = str(strategy_id or "").strip()
    if not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id is required")

    try:
        parsed = parsed_balance
        if parsed is None:
            api = _get_api()
            parsed = _parse_balance(_get_balance_data(api, allow_cache=False))
        from src.dashboard.routes.account import _attach_holding_strategies

        _attach_holding_strategies(parsed)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kiwoom balance API request failed: {exc}") from exc

    target_symbol = str(symbol or "").strip()
    orders = []
    skipped = []
    if active_symbols is None:
        active_symbols = _unsubmitted_dashboard_sell_symbols()
    source = "dashboard_strategy_holding_sell" if target_symbol else "dashboard_strategy_sell_all"
    for holding in parsed.get("holdings", []):
        holding_symbol = str(holding.get("symbol") or "").strip()
        if target_symbol and holding_symbol != target_symbol:
            continue
        allocation = next(
            (
                item for item in holding.get("strategy_allocations", [])
                if str(item.get("strategy_id") or "") == strategy_id
            ),
            None,
        )
        if not allocation:
            continue
        allocated_qty = _to_int(allocation.get("allocated_qty"))
        holding_qty = _to_int(holding.get("qty"))
        sellable_qty = _to_int(holding.get("sellable_qty", holding.get("qty")))
        qty = min(allocated_qty, sellable_qty)
        if holding_symbol in active_symbols:
            skipped.append({
                "symbol": holding_symbol,
                "name": str(holding.get("name") or holding_symbol),
                "strategy_id": strategy_id,
                "qty": allocated_qty,
                "sellable_qty": sellable_qty,
                "reason": "active sell request already exists",
            })
            continue
        if qty <= 0:
            skipped.append({
                "symbol": holding_symbol,
                "name": str(holding.get("name") or holding_symbol),
                "strategy_id": strategy_id,
                "qty": allocated_qty,
                "sellable_qty": sellable_qty,
                "reason": "sellable attributed quantity is zero",
            })
            continue
        strategy_name = str(allocation.get("strategy_name") or strategy_id)
        orders.append({
            "symbol": holding_symbol,
            "name": str(holding.get("name") or holding_symbol),
            "action": "sell",
            "qty": qty,
            "price": 0,
            "reason": f"dashboard strategy attribution sell: {strategy_name}",
            "source": source,
            "strategy_id": None if strategy_id == "unattributed" else strategy_id,
            # Snapshot metadata is returned to the operator and deliberately not
            # used as authority at fill time.  Broker quantity remains the
            # account truth while strategy attribution identifies only this
            # strategy's portion of a shared holding.
            "broker_qty_snapshot": holding_qty,
            "sellable_qty_snapshot": sellable_qty,
            "allocated_qty_snapshot": allocated_qty,
            "shared_qty_snapshot": max(0, holding_qty - allocated_qty),
            "expected_remaining_attribution": max(0, allocated_qty - qty),
        })
    if target_symbol and not orders and not skipped:
        raise HTTPException(
            status_code=404,
            detail=f"strategy attribution not found for {target_symbol}: {strategy_id}",
        )
    return orders, skipped


def _prepare_strategy_attribution_sells(
    strategy_id: str,
    *,
    symbol: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Create a fail-closed liquidation snapshot without submitting an order.

    Open buys are canceled first so a late buy fill cannot recreate exposure
    after the strategy liquidation.  A fresh, uncached broker balance is then
    combined with the strategy ledger while the request lock protects the
    duplicate-approval check and subsequent approval creation.
    """
    try:
        api = _get_api()
        canceled_buy_orders = _cancel_open_buy_orders_before_liquidation(api)
        cancel_failures = [
            item for item in canceled_buy_orders
            if str(item.get("status") or "") == "cancel_failed"
        ]
        if cancel_failures:
            order_ids = ", ".join(
                str(item.get("broker_order_id") or "unknown") for item in cancel_failures
            )
            raise HTTPException(
                status_code=409,
                detail=f"open buy cancellation must be confirmed before strategy liquidation: {order_ids}",
            )
        parsed = _parse_balance(_get_balance_data(api, allow_cache=False))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"strategy liquidation preflight failed: {exc}") from exc

    active_symbols = _unsubmitted_dashboard_sell_symbols()
    orders, skipped = _strategy_attribution_sell_orders(
        strategy_id,
        symbol=symbol,
        parsed_balance=parsed,
        active_symbols=active_symbols,
    )
    return orders, skipped, canceled_buy_orders


def _queue_strategy_attribution_sells(
    orders: list[dict],
    skipped: list[dict],
    *,
    canceled_buy_orders: list[dict] | None = None,
) -> dict:
    approval_ids = [_create_approval_row(order) for order in orders]
    auto_approval_queued = False
    if approval_ids and _auto_approval_enabled():
        _run_auto_approval_batch_async(approval_ids)
        auto_approval_queued = True
    _clear_balance_cache()
    return {
        "status": "created" if approval_ids else "empty",
        "created_count": len(approval_ids),
        "pending_count": len(approval_ids),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "canceled_buy_orders": canceled_buy_orders or [],
        "preflight_snapshot": [{
            key: order.get(key) for key in (
                "symbol", "broker_qty_snapshot", "sellable_qty_snapshot",
                "allocated_qty_snapshot", "shared_qty_snapshot",
                "expected_remaining_attribution",
            )
        } for order in orders],
        "post_fill_verification_required": bool(approval_ids),
        "orders": [{"id": approval_id, "status": "pending"} for approval_id in approval_ids],
        "auto_approval_queued": auto_approval_queued,
        "fill_status_note": "키움 주문 접수 후 실제 체결 여부는 주문내역 동기화에서 확정됩니다.",
    }


@router.post("/api/holdings/strategy-sell")
def sell_holding_strategy_attribution(payload: dict = Body(...)):
    missing = _required_env_missing()
    if missing:
        raise HTTPException(status_code=503, detail=f"Missing environment variables: {', '.join(missing)}")
    strategy_id = str(payload.get("strategy_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    with _holding_sell_request_lock:
        orders, skipped, canceled = _prepare_strategy_attribution_sells(strategy_id, symbol=symbol)
        return _queue_strategy_attribution_sells(
            orders, skipped, canceled_buy_orders=canceled
        )


@router.post("/api/holdings/strategy-sell-all")
def sell_all_strategy_attribution(payload: dict = Body(...)):
    missing = _required_env_missing()
    if missing:
        raise HTTPException(status_code=503, detail=f"Missing environment variables: {', '.join(missing)}")
    strategy_id = str(payload.get("strategy_id") or "").strip()
    with _holding_sell_request_lock:
        orders, skipped, canceled = _prepare_strategy_attribution_sells(strategy_id)
        return _queue_strategy_attribution_sells(
            orders, skipped, canceled_buy_orders=canceled
        )




@router.post("/api/approvals/{approval_id}/approve")
def approve_order(approval_id: int):
    from fastapi import HTTPException
    from src.application.orders.health import NewRiskBlockedError

    try:
        return _approve_pending_approval(approval_id, "수동승인")
    except NewRiskBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc




@router.post("/api/approvals/{approval_id}/reject")
def reject_order(approval_id: int):
    item = _load_pending_approval(approval_id)
    if item.get("managed_order_id"):
        from src.strategy.autonomy.ai_stock_integration import (
            reject_managed_ai_stock_order,
        )

        try:
            return reject_managed_ai_stock_order(approval_id)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"managed AI-stock rejection failed closed: {exc}",
            ) from exc
    from src.application.orders.legacy_bridge import ensure_approval_order, mirror_status

    ledger_order = ensure_approval_order(trader.connect_db, item)
    now = trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S")
    with trader.connect_db() as conn:
        conn.execute(
            "UPDATE approvals SET status = 'rejected', response_msg = 'Rejected by dashboard', updated_at = ? WHERE id = ?",
            (now, approval_id),
        )
    mirror_status(
        trader.connect_db, ledger_order, "rejected",
        actor="dashboard", reason="approval rejected",
    )
    return {"id": approval_id, "status": "rejected"}




@router.post("/api/trades/order-status/sync")
def sync_trade_order_status(days: int = 30):
    if trader.config.dry_run:
        raise HTTPException(status_code=400, detail="Order status sync requires DRY_RUN=false")
    try:
        result = _sync_order_status_from_history(_get_api(), days=days)
        _clear_balance_cache()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e



_AMBIGUOUS_ORDER_ERROR_MARKERS = (
    "connection aborted",
    "connection reset",
    "remote disconnected",
    "remotedisconnected",
    "remote end closed",
    "readtimeout",
    "connecttimeout",
    "timed out",
    "timeout",
    "시간 초과",
)


def _is_ambiguous_order_failure(message: object) -> bool:
    normalized = str(message or "").strip().lower()
    return bool(normalized) and any(
        marker in normalized for marker in _AMBIGUOUS_ORDER_ERROR_MARKERS
    )


def _reconcile_ambiguous_orders_from_balance(current_holdings: dict) -> dict:
    """Promote response-lost orders only when their total exactly explains the balance gap."""
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        confirmed_rows = conn.execute(
            """
            SELECT * FROM trades
            WHERE COALESCE(env, ?) = ? AND COALESCE(ok, 0) = 1
            ORDER BY ts ASC, id ASC
            """,
            (trader.config.trading_env, trader.config.trading_env),
        ).fetchall()
        unresolved_rows = conn.execute(
            """
            SELECT * FROM trades
            WHERE COALESCE(env, ?) = ?
              AND COALESCE(broker_order_id, '') = ''
              AND COALESCE(order_status, '') IN ('failed', 'broker_unknown')
              AND COALESCE(filled_qty, 0) = 0
            ORDER BY ts ASC, id ASC
            """,
            (trader.config.trading_env, trader.config.trading_env),
        ).fetchall()

        positions = {}
        average_costs = {}
        for trade in _account_trades([dict(row) for row in confirmed_rows]):
            symbol = str(trade.get("symbol") or "")
            qty = _to_int(trade.get("qty"))
            price = _to_int(trade.get("price"))
            position = positions.get(symbol, 0)
            if trade.get("action") == "buy":
                new_position = position + qty
                previous_cost = average_costs.get(symbol, 0.0)
                average_costs[symbol] = (
                    ((position * previous_cost) + (qty * price)) / new_position
                    if new_position > 0 else 0.0
                )
                positions[symbol] = new_position
            elif trade.get("action") == "sell":
                positions[symbol] = max(0, position - qty)
                if positions[symbol] == 0:
                    average_costs[symbol] = 0.0

        groups = {}
        for row in unresolved_rows:
            item = dict(row)
            if not _is_ambiguous_order_failure(item.get("response_msg")):
                continue
            key = (str(item.get("symbol") or ""), str(item.get("action") or ""))
            groups.setdefault(key, []).append(item)

        reconciled_items = []
        for (symbol, action), rows in groups.items():
            local_qty = positions.get(symbol, 0)
            broker_holding = current_holdings.get(symbol) or {}
            broker_qty = _to_int(broker_holding.get("qty"))
            balance_gap = broker_qty - local_qty
            expected_qty = balance_gap if action == "buy" else -balance_gap
            group_qty = sum(_to_int(row.get("qty")) for row in rows)
            if expected_qty <= 0 or group_qty != expected_qty:
                continue

            raw_holding = broker_holding.get("_raw") or {}
            fallback_price = _to_int(
                raw_holding.get("pchs_avg_pric")
                or broker_holding.get("price")
                or average_costs.get(symbol)
            )
            for row in rows:
                qty = _to_int(row.get("qty"))
                price = _to_int(row.get("price")) or fallback_price
                previous_message = str(row.get("response_msg") or "").strip()
                message = (
                    f"{previous_message} | 증권사 잔고 기준 응답 유실 주문 보정"
                    if previous_message else "증권사 잔고 기준 응답 유실 주문 보정"
                )
                conn.execute(
                    """
                    UPDATE trades
                    SET ok = 1, order_status = 'reconciled', filled_qty = ?,
                        filled_price = ?, price = ?, response_msg = ?
                    WHERE id = ?
                    """,
                    (qty, price, price, message, int(row["id"])),
                )
                source_approval_id = _to_int(row.get("source_approval_id"))
                if source_approval_id > 0:
                    conn.execute(
                        """
                        UPDATE approvals
                        SET status = 'executed', response_msg = ?, updated_at = ?
                        WHERE id = ? AND status = 'broker_unknown'
                        """,
                        (
                            message,
                            trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S"),
                            source_approval_id,
                        ),
                    )
                reconciled_items.append({
                    "sync_type": "balance",
                    "sync_result": "response_loss_reconciled",
                    "ts": row.get("ts") or "",
                    "symbol": symbol,
                    "name": row.get("name") or symbol,
                    "action": action,
                    "qty": qty,
                    "price": price,
                    "broker_order_id": "",
                    "order_status": "reconciled",
                    "message": message,
                })

    return {
        "ok": True,
        "reconciled_count": len(reconciled_items),
        "items": reconciled_items,
    }


def _remove_non_broker_trade_rows() -> dict:
    """Remove local-only rows that must not survive a broker-authoritative sync."""
    removable_statuses = (
        "failed",
        "canceled",
        "rejected",
        "simulated",
    )
    placeholders = ",".join("?" for _ in removable_statuses)
    with trader.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, ts, symbol, name, action, qty, price, order_status, response_msg
            FROM trades
            WHERE COALESCE(env, ?) = ?
              AND COALESCE(broker_order_id, '') = ''
              AND (
                    COALESCE(order_status, '') IN ({placeholders})
                    OR COALESCE(ok, 0) = 0
                  )
            ORDER BY ts ASC
            """,
            (trader.config.trading_env, trader.config.trading_env, *removable_statuses),
        ).fetchall()
        removable_rows = [
            row for row in rows
            if not (
                str(row["order_status"] or "") in {"failed", "broker_unknown"}
                and _is_ambiguous_order_failure(row["response_msg"])
            )
        ]
        removable_ids = [int(row["id"]) for row in removable_rows]
        if removable_ids:
            id_placeholders = ",".join("?" for _ in removable_ids)
            cursor = conn.execute(
                f"DELETE FROM trades WHERE id IN ({id_placeholders})",
                removable_ids,
            )
            removed_count = int(cursor.rowcount)
        else:
            removed_count = 0
    return {
        "ok": True,
        "removed_count": removed_count,
        "scope": "local rows without broker order id",
        "items": [
            {
                **dict(row),
                "sync_type": "cleanup",
                "sync_result": "removed",
                "message": row["response_msg"] or "증권사 주문번호가 없는 불일치 기록 정리",
            }
            for row in removable_rows
        ],
    }



def _save_trade_sync_result(result: dict) -> None:
    from src.db.trade_repository import save_trade_sync_run

    payload = {
        key: result.get(key)
        for key in (
            "ok",
            "synced_count",
            "balance_synced_count",
            "history_imported_count",
            "history_updated_count",
            "response_loss_reconciled_count",
            "removed_mismatch_count",
            "history_error",
            "order_status_error",
            "terminal_regression_count",
            "review_required_count",
            "sync_items",
            "status",
            "error",
            "started_at",
        )
    }
    now = trader.datetime.now(trader.KST).isoformat()
    run_id = str(result.get("run_id") or now)
    completed_at = result.get("completed_at")
    if not completed_at and result.get("status") != "running":
        completed_at = now
    payload.update({
        "run_id": run_id,
        "started_at": result.get("started_at") or now,
        "completed_at": completed_at,
    })
    save_trade_sync_run(payload)


def _classify_trade_sync_outcome(
    *,
    sync_items: list[dict],
    history_sync: dict | None,
    order_status_sync: dict | None,
    history_error: str | None,
    order_status_error: str | None,
) -> dict:
    """Return a fail-closed outcome for a completed reconciliation attempt."""
    terminal_regression_count = (
        _to_int((history_sync or {}).get("terminal_regression_count"))
        + _to_int((order_status_sync or {}).get("terminal_regression_count"))
    )
    review_required_count = sum(
        1 for item in sync_items
        if str(item.get("sync_result") or "") == "review_required"
    )
    component_errors = any(
        str(error or "").strip()
        for error in (history_error, order_status_error)
    )
    component_unhealthy = any(
        component is not None and not bool(component.get("ok", False))
        for component in (history_sync, order_status_sync)
    )
    if review_required_count:
        status = "review_required"
    elif component_errors or component_unhealthy or terminal_regression_count:
        status = "partial"
    else:
        status = "success"
    return {
        "status": status,
        "ok": status == "success",
        "terminal_regression_count": terminal_regression_count,
        "review_required_count": review_required_count,
    }


def _migrate_trade_sync_file_to_db() -> None:
    """Import pre-DB runtime history once for backward compatibility."""
    from src.db.trade_repository import list_trade_sync_runs, save_trade_sync_run

    if list_trade_sync_runs(limit=1) or not TRADE_SYNC_RESULT_PATH.exists():
        return
    try:
        previous = json.loads(TRADE_SYNC_RESULT_PATH.read_text(encoding="utf-8"))
        file_runs = previous.get("runs") if isinstance(previous.get("runs"), list) else [previous]
        for index, item in enumerate(file_runs):
            if not isinstance(item, dict) or not item.get("completed_at"):
                continue
            completed_at = str(item["completed_at"])
            save_trade_sync_run({
                **item,
                "run_id": str(item.get("run_id") or f"legacy-{completed_at}-{index}"),
                "started_at": str(item.get("started_at") or completed_at),
                "status": str(item.get("status") or ("completed" if item.get("ok") else "failed")),
            })
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to migrate trade sync runtime history: {exc}")


@router.get("/api/trades/sync/status")
def get_trade_sync_status():
    from src.db.trade_repository import list_trade_sync_runs


    _migrate_trade_sync_file_to_db()
    runs = list_trade_sync_runs(limit=50)
    if not runs:
        return {"ok": True, "available": False, "runs": []}
    if runs[0].get("status") == "running":
        with _trade_sync_lock:
            thread_alive = _trade_sync_thread is not None and _trade_sync_thread.is_alive()
        if not thread_alive:
            interrupted = {
                **runs[0],
                "status": "failed",
                "ok": False,
                "error": runs[0].get("error") or "서버 재기동으로 동기화가 중단되었습니다.",
                "completed_at": trader.datetime.now(trader.KST).isoformat(),
            }
            _save_trade_sync_result(interrupted)
            runs[0] = interrupted
    summaries = [
        {
            **run,
            "sync_item_count": len(run.get("sync_items") or []),
            "sync_items": [],
        }
        for run in runs
    ]
    return {"available": True, **summaries[0], "runs": summaries}


@router.get("/api/trades/sync/runs/{run_id}")
def get_trade_sync_run_detail(run_id: str):
    from src.db.trade_repository import get_trade_sync_run

    run = get_trade_sync_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="동기화 실행 기록을 찾을 수 없습니다.")
    return run




def _execute_trade_sync(*, days: int, run_id: str, started_at: str) -> dict:
    _refresh_legacy_dependencies()
    if trader.config.dry_run:
        message = "모의 실행(DRY_RUN) 모드에서는 증권사 계좌 동기화를 사용할 수 없습니다."
        _save_trade_sync_result({
            "run_id": run_id,
            "started_at": started_at,
            "status": "failed",
            "ok": False,
            "error": message,
            "sync_items": [],
        })
        raise HTTPException(status_code=400, detail=message)
    _save_trade_sync_result({
        "run_id": run_id,
        "started_at": started_at,
        "status": "running",
        "ok": False,
        "sync_items": [],
    })
    try:

        api = _get_api()
        shared_history = None
        shared_history_error = None
        try:
            start_date, end_date = _order_history_window(days)
            shared_history = api.get_trade_history(start_date, end_date)
        except Exception as exc:
            shared_history_error = str(exc)

        order_status_sync = None
        order_status_error = None
        if shared_history is not None:
            try:
                order_status_sync = _sync_order_status_from_history(
                    api,
                    days=days,
                    history=shared_history,
                )
            except Exception as exc:
                order_status_error = str(exc)
        else:
            order_status_error = shared_history_error

        history_sync = None
        history_error = shared_history_error
        if shared_history is not None:
            try:
                history_sync = _sync_filled_trades_from_history(
                    api,
                    days=days,
                    history=shared_history,
                )
            except Exception as exc:
                history_error = str(exc)

        balance_data = _get_balance_data(api, allow_cache=False)
        parsed_balance = _parse_balance(balance_data)
        current_holdings = {h['symbol']: h for h in parsed_balance['holdings']}
        response_loss_reconciliation = _reconcile_ambiguous_orders_from_balance(
            current_holdings
        )
        cleanup = _remove_non_broker_trade_rows()

        # Reconstruct current holdings from DB and Cloud
        cloud_trades = fetch_cloud_trades() or []
        local_trades = []
        with trader.connect_db() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY ts ASC").fetchall()
            local_trades = [dict(row) for row in rows]

        merged_trades = {}
        for t in cloud_trades + local_trades:
            ts = t.get("ts") or t.get("timestamp")
            if not ts: continue
            key = (
                f"{ts}_{t.get('symbol')}_{t.get('action')}_"
                f"{t.get('strategy_id') or ''}_{t.get('qty') or 0}_"
                f"{t.get('broker_order_id') or ''}"
            )
            merged_trades[key] = t

        merged_values = sorted(merged_trades.values(), key=lambda x: x.get("ts", ""))
        trades = _account_trades(merged_values)
        strategy_positions = _strategy_position_quantities(local_trades)
        from src.application.orders.identity import broker_account_scope_key

        # positions is the canonical current projection. Replaying every legacy
        # trade and then adding all historical alignment deltas double-counts
        # repeated reconciliations and recreates ever-larger false mismatches.
        names = {}
        for t in trades:
            if not t.get("ok", False): continue
            sym = t["symbol"]
            names[sym] = t.get("name", sym)

        with trader.connect_db() as conn:
            rows = conn.execute(
                """SELECT symbol,quantity FROM positions
                   WHERE account_key=? AND market='KR' AND quantity>0""",
                (broker_account_scope_key("KR"),),
            ).fetchall()
        db_holdings = {str(symbol): int(quantity) for symbol, quantity in rows}

        synced_count = 0
        balance_sync_items = []

        # 1. Sync missing buys (broker has more)
        for sym, ch in current_holdings.items():
            broker_qty = ch["qty"]
            db_qty = db_holdings.get(sym, 0)
            diff = broker_qty - db_qty

            if diff != 0:
                action = "buy" if diff > 0 else "sell"
                raw_stock = ch.get("_raw", {})
                price = int(float(raw_stock.get("pchs_avg_pric", ch["price"])))

                issue_id = _record_reconciliation_issue(
                    symbol=sym,
                    broker_qty=broker_qty,
                    internal_qty=db_qty,
                    reason="broker balance differs from verified fills",
                    snapshot={"holding": ch, "strategy_positions": strategy_positions.get(sym, {})},
                )
                balance_sync_items.append({
                    "sync_type": "balance",
                    "sync_result": "review_required",
                    "ts": trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": sym, "name": ch["name"], "action": action,
                    "qty": abs(diff), "price": price, "broker_order_id": "",
                    "order_status": "reconciliation_open", "issue_id": issue_id,
                    "message": f"증권사 {broker_qty}주 / 검증 체결 원장 {db_qty}주 차이: 자동 체결 생성 안 함",
                })

        # Calculate db average costs to use for selling missing items without affecting PnL
        db_costs = {}
        for t in trades:
            if not t.get("ok", False): continue
            sym = t["symbol"]
            qty = t["qty"]
            price = t["price"]

            if sym not in db_costs: db_costs[sym] = {"qty": 0, "cost": 0.0}
            if t["action"] == "buy":
                total_qty = db_costs[sym]["qty"] + qty
                total_cost = (db_costs[sym]["qty"] * db_costs[sym]["cost"]) + (qty * price)
                db_costs[sym]["qty"] = total_qty
                db_costs[sym]["cost"] = total_cost / total_qty if total_qty > 0 else 0
            elif t["action"] == "sell":
                db_costs[sym]["qty"] = max(0, db_costs[sym]["qty"] - qty)
                if db_costs[sym]["qty"] <= 0: db_costs[sym]["cost"] = 0

        # 2. Sync missing sells (broker has less or none)
        for sym, db_qty in db_holdings.items():
            if db_qty > 0 and sym not in current_holdings:
                avg_cost = int(db_costs.get(sym, {}).get("cost", 0))
                issue_id = _record_reconciliation_issue(
                    symbol=sym,
                    broker_qty=0,
                    internal_qty=db_qty,
                    reason="verified fills contain a position absent from broker balance",
                    snapshot={"name": names.get(sym, sym), "strategy_positions": strategy_positions.get(sym, {})},
                )
                balance_sync_items.append({
                    "sync_type": "balance", "sync_result": "review_required",
                    "ts": trader.datetime.now(trader.KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": sym, "name": names.get(sym, sym), "action": "sell",
                    "qty": db_qty, "price": avg_cost, "broker_order_id": "",
                    "order_status": "reconciliation_open", "issue_id": issue_id,
                    "message": "증권사에 없는 내부 포지션: 자동 매도 체결 생성 안 함",
                })

        imported_count = _to_int(history_sync.get("imported_count")) if history_sync else 0
        updated_count = _to_int(history_sync.get("updated_count")) if history_sync else 0
        # 동기화로 보유/거래가 바뀌었으니 잔고·파생 보유탭 스냅샷을 무효화해 현행화한다.
        _clear_balance_cache()
        history_items = list((history_sync or {}).get("items") or [])
        order_status_items = [
            {
                "sync_type": "order_status",
                "sync_result": "updated" if item.get("balance_confirmed", True) else "checked",
                "ts": "",
                "symbol": item.get("symbol", ""),
                "name": item.get("name", ""),
                "action": item.get("action", ""),
                "qty": item.get("filled_qty", 0),
                "price": item.get("filled_price", 0),
                "broker_order_id": item.get("broker_order_id", ""),
                "order_status": item.get("order_status", ""),

                "message": "주문 상태 확인",
            }
            for item in ((order_status_sync or {}).get("orders") or [])
        ]
        response_loss_items = list(response_loss_reconciliation.get("items") or [])
        sync_items = history_items + order_status_items + response_loss_items + balance_sync_items + list(cleanup.get("items") or [])
        outcome = _classify_trade_sync_outcome(
            sync_items=sync_items,
            history_sync=history_sync,
            order_status_sync=order_status_sync,
            history_error=history_error,
            order_status_error=order_status_error,
        )
        response = {
            "run_id": run_id,
            "started_at": started_at,
            "status": outcome["status"],
            "ok": outcome["ok"],
            "synced_count": synced_count + imported_count,
            "balance_synced_count": synced_count,
            "history_imported_count": imported_count,
            "history_updated_count": updated_count,
            "response_loss_reconciled_count": response_loss_reconciliation["reconciled_count"],
            "history_sync": history_sync,
            "history_error": history_error,
            "order_status_sync": order_status_sync,
            "order_status_error": order_status_error,
            "terminal_regression_count": outcome["terminal_regression_count"],
            "review_required_count": outcome["review_required_count"],
            "removed_mismatch_count": cleanup["removed_count"],
            "cleanup": cleanup,
            "sync_items": sync_items,
        }
        _save_trade_sync_result(response)
        return response
    except Exception as e:
        _save_trade_sync_result({
            "run_id": run_id,
            "started_at": started_at,
            "status": "failed",
            "ok": False,
            "error": str(e),
            "sync_items": [],
        })
        logger.exception("Broker trade synchronization failed")
        return {
            "run_id": run_id,
            "started_at": started_at,
            "status": "failed",
            "ok": False,
            "error": str(e),
            "sync_items": [],
        }


def _run_trade_sync_background(*, days: int, run_id: str, started_at: str) -> None:
    global _trade_sync_thread
    logger.info(f"[TRADE_SYNC] started run_id={run_id} days={days}")
    try:
        result = _execute_trade_sync(days=days, run_id=run_id, started_at=started_at)
        logger.info(
            f"[TRADE_SYNC] finished run_id={run_id} "
            f"status={result.get('status')} synced={result.get('synced_count', 0)} "
            f"error={result.get('error') or '-'}"
        )
    finally:
        with _trade_sync_lock:
            _trade_sync_thread = None


@router.post("/api/trades/sync")

def sync_trades(days: int = 30):
    global _trade_sync_thread
    started_at = trader.datetime.now(trader.KST).isoformat()
    run_id = started_at
    if trader.config.dry_run:
        message = "DRY_RUN 모드에서는 증권사 계좌 동기화를 실행할 수 없습니다."
        _save_trade_sync_result({
            "run_id": run_id,
            "started_at": started_at,
            "status": "failed",
            "ok": False,
            "error": message,
            "sync_items": [],
        })
        raise HTTPException(status_code=400, detail=message)

    with _trade_sync_lock:
        if _trade_sync_thread is not None and _trade_sync_thread.is_alive():
            raise HTTPException(status_code=409, detail="증권사 기록 동기화가 이미 실행 중입니다.")
        _save_trade_sync_result({
            "run_id": run_id,
            "started_at": started_at,
            "status": "running",
            "ok": False,
            "sync_items": [],
        })
        thread = threading.Thread(
            target=_run_trade_sync_background,
            kwargs={"days": days, "run_id": run_id, "started_at": started_at},
            name="broker-trade-sync",
            daemon=True,
        )
        _trade_sync_thread = thread
        thread.start()

    return {
        "run_id": run_id,
        "started_at": started_at,
        "status": "running",
        "ok": True,
    }



@router.get("/api/trades")
def get_trades(limit: int = 50, strategy_id: str | None = None):
    try:
        cloud_trades = fetch_cloud_trades() or []
        local_trades = []
        with trader.connect_db() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY ts ASC").fetchall()
            local_trades = [dict(row) for row in rows]
            for trade in local_trades:
                trade["_local_id"] = trade.get("id")

        from src.db.trade_repository import list_local_trade_cleanup_candidates

        cleanup_by_id = {
            int(item["id"]): item

            for item in list_local_trade_cleanup_candidates(max(limit, 200))
        }

        merged_trades = {}
        for t in cloud_trades + local_trades:
            ts = t.get("ts") or t.get("timestamp")
            if not ts: continue
            raw_local_id = t.get("_local_id")
            local_id = int(raw_local_id) if str(raw_local_id or "").isdigit() else None
            cleanup_item = cleanup_by_id.get(local_id) or {}
            key = f"{ts}_{t.get('symbol')}_{t.get('action')}"
            merged_trades[key] = {
                "local_id": local_id,
                "ts": ts,
                "symbol": t.get("symbol"),
                "name": t.get("name", t.get("symbol")),
                "action": t.get("action"),
                "qty": t.get("qty", 0),
                "price": t.get("price", 0),
                "reason": t.get("reason", ""),
                "ok": t.get("ok", 1),
                "env": t.get("env", "demo"),
                "dry_run": t.get("dry_run", 0),
                "broker_order_id": t.get("broker_order_id", ""),
                "order_status": t.get("order_status", ""),
                "filled_qty": _to_int(t.get("filled_qty")),
                "filled_price": _to_int(t.get("filled_price")),
                "response_msg": t.get("response_msg", ""),
                "strategy_id": t.get("strategy_id", ""),
                "strategy_version": t.get("strategy_version"),
                "profile_hash": t.get("profile_hash", ""),
                "source_approval_id": t.get("source_approval_id"),
                "cleanup_reason": cleanup_item.get("cleanup_reason"),
                "cleanup_risk": cleanup_item.get("cleanup_risk"),
            }

        trades = _account_trades(list(merged_trades.values()))
        if strategy_id:
            trades = [trade for trade in trades if str(trade.get("strategy_id") or "") == strategy_id]
        trades = sorted(trades, key=lambda x: x["ts"], reverse=True)
        return {"trades": trades[:limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trades/local-cleanup")
def get_local_trade_cleanup_candidates(limit: int = 200):
    from src.db.trade_repository import list_local_trade_cleanup_candidates

    return {"trades": list_local_trade_cleanup_candidates(limit)}


@router.delete("/api/trades/local/{trade_id}")
def delete_local_trade(trade_id: int, confirm: bool = False):
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required")

    from src.db.trade_repository import delete_local_trade_record

    try:

        deleted = delete_local_trade_record(trade_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "deleted_id": trade_id,
        "scope": "local_only",
        "symbol": deleted.get("symbol"),
        "order_status": deleted.get("order_status"),
    }
