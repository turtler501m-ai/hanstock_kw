from __future__ import annotations

"""
Seven Split auto-trading engine (Refactored).
"""
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to sys.path to allow running as a script directly
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import config, get_settings, settings_snapshot, trading_flags
from src.utils.logger import logger
from src.db.repository import init_db, connect_db, save_trade, update_trade_order_status
from src.notifier.slack import slack_session_start, slack_order, slack_candidates, slack_session_end, slack_error
from src.strategy.seven_split import (
    WATCHLIST, KOSPI_UNIVERSE, STOCK_NAMES,
    generate_signal, build_scan_universe, find_candidates, build_orders,
    generate_ai_weight_plan, generate_portfolio_optimizer_plan,
    calc_strategy_profile,
    is_excluded_symbol,
)
from src.strategy.indicators import calc_bollinger, calc_macd, calc_rsi, calc_sma
from src.strategy.risk import RiskEngine
from src.strategy.router import OrderRouter
from src.broker.models import AccountBalance, Holding
from src.execution_plan import (
    PlanRow,
    signal_to_plan_row,
    candidate_order_to_plan_row,
    build_execution_plan,
)
from src.strategy_ids import AI_REBALANCE_STRATEGY_ID, ISOLATED_STOCK_STRATEGY_IDS

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class TraderRuntimeContext:
    """Immutable settings snapshot used by one trading-engine run."""

    settings: object
    flags: object

    @classmethod
    def capture(cls) -> "TraderRuntimeContext":
        settings = settings_snapshot()
        return cls(settings=settings, flags=trading_flags(settings))

ONLINE_ACCESS_BLOCKED = config.online_access_blocked

SPLIT_N = config.split_n
STOP_LOSS_PCT = config.stop_loss_pct
TAKE_PROFIT = config.take_profit
RSI_BUY = config.rsi_buy
RSI_SELL = config.rsi_sell

MAX_SINGLE_WEIGHT = config.max_single_weight
CASH_BUFFER = config.cash_buffer
MAX_DAILY_LOSS_PCT = config.max_daily_loss_pct
SCAN_UNIVERSE_SIZE = config.scan_universe_size

def runtime_flags():
    return TraderRuntimeContext.capture().flags


def sync_legacy_config_aliases() -> None:
    """Refresh the few remaining legacy client/strategy constants.

    Runtime safety and allocation values are deliberately not mirrored as
    mutable module globals; callers must capture ``TraderRuntimeContext``.
    """
    global ONLINE_ACCESS_BLOCKED
    global SPLIT_N, STOP_LOSS_PCT, TAKE_PROFIT, RSI_BUY, RSI_SELL
    global MAX_SINGLE_WEIGHT, CASH_BUFFER
    global MAX_DAILY_LOSS_PCT, SCAN_UNIVERSE_SIZE

    settings = get_settings()
    flags = trading_flags(settings)
    ONLINE_ACCESS_BLOCKED = flags.online_access_blocked
    SPLIT_N = settings.split_n
    STOP_LOSS_PCT = settings.stop_loss_pct
    TAKE_PROFIT = settings.take_profit
    RSI_BUY = settings.rsi_buy
    RSI_SELL = settings.rsi_sell
    MAX_SINGLE_WEIGHT = settings.max_single_weight
    CASH_BUFFER = settings.cash_buffer
    MAX_DAILY_LOSS_PCT = settings.max_daily_loss_pct
    SCAN_UNIVERSE_SIZE = settings.scan_universe_size
    if "_LEGACY_SYNCED_VALUES" in globals():
        _LEGACY_SYNCED_VALUES.update({
            name: globals()[name]
            for name in _LEGACY_SYNCED_VALUES
        })

RUNTIME_DIR = Path(".runtime")
DB_PATH = Path(config.trade_db_path)

_LEGACY_SYNCED_VALUES = {
    name: globals()[name]
    for name in (
        "ONLINE_ACCESS_BLOCKED",
        "MAX_DAILY_LOSS_PCT",
    )
}


def _runtime_value(alias: str, settings_value):
    legacy_value = globals()[alias]
    if legacy_value != _LEGACY_SYNCED_VALUES.get(alias):
        return legacy_value
    return settings_value


def operating_capital(account_total_eval: int | float = 0, *, runtime: TraderRuntimeContext | None = None) -> int:
    """Return the configured capital available to Hanstock for this account."""
    settings = (runtime or TraderRuntimeContext.capture()).settings
    configured_value = settings.total_capital
    configured = max(0, int(configured_value or 0))
    account_total = max(0, int(account_total_eval or 0))
    if configured <= 0:
        return account_total
    if account_total <= 0:
        return configured
    return min(configured, account_total)


def available_buying_cash(
    broker_cash: int | float,
    stock_eval: int | float,
    account_total_eval: int | float,
    *,
    runtime: TraderRuntimeContext | None = None,
) -> int:
    """Cap new buys by configured capital, cash buffer, and current exposure."""
    runtime = runtime or TraderRuntimeContext.capture()
    settings = runtime.settings
    capital = operating_capital(account_total_eval, runtime=runtime)
    cash_buffer = float(settings.cash_buffer or 0)
    investable_limit = int(capital * max(0.0, 1.0 - cash_buffer))
    remaining_exposure = max(0, investable_limit - max(0, int(stock_eval or 0)))
    return min(max(0, int(broker_cash or 0)), remaining_exposure)


def buying_cash_diagnostics(
    broker_cash: int | float,
    stock_eval: int | float,
    account_total_eval: int | float,
    *,
    locked_holding_eval: int | float = 0,
    runtime: TraderRuntimeContext | None = None,
) -> dict:
    """Expose why new-buy cash is capped for dashboard/log diagnostics."""
    runtime = runtime or TraderRuntimeContext.capture()
    settings = runtime.settings
    capital = operating_capital(account_total_eval, runtime=runtime)
    cash_buffer = float(settings.cash_buffer or 0)
    investable_limit = int(capital * max(0.0, 1.0 - cash_buffer))
    exposure_for_new_buys = max(0, int(stock_eval or 0) - int(locked_holding_eval or 0))
    exposure_remaining = investable_limit - exposure_for_new_buys
    broker_cash_int = max(0, int(broker_cash or 0))
    return {
        "broker_cash": broker_cash_int,
        "stock_eval": max(0, int(stock_eval or 0)),
        "locked_holding_eval": max(0, int(locked_holding_eval or 0)),
        "exposure_for_new_buys": exposure_for_new_buys,
        "operating_capital": capital,
        "cash_buffer": cash_buffer,
        "investable_limit": investable_limit,
        "exposure_remaining": exposure_remaining,
        "buying_cash": min(broker_cash_int, max(0, exposure_remaining)),
    }


def build_market_data_api(
    broker_api,
    *,
    runtime: TraderRuntimeContext | None = None,
) :
    api_runtime = getattr(broker_api, "runtime", None)
    runtime = runtime or (api_runtime if isinstance(api_runtime, TraderRuntimeContext) else None)
    runtime = runtime or TraderRuntimeContext.capture()
    return broker_api



_CANDIDATE_INDICATOR_KEYS = {
    "rsi", "rsi2", "sma20", "sma60", "bb_lo", "bb_hi", "macd_hist",
    "strategy_id", "strategy_risk",
}

_VALID_RUN_MODES = {"analysis_only", "live", None}
_ISOLATED_STRATEGY_IDS = ISOLATED_STOCK_STRATEGY_IDS


def normalize_run_mode(mode: str | None) -> str | None:
    if mode not in _VALID_RUN_MODES:
        raise ValueError(f"Invalid run mode: {mode!r}. Must be one of {_VALID_RUN_MODES}")
    return mode


def check_secrets():
    pass


def init_approval_db() -> None:
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    with connect_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price INTEGER NOT NULL,
                reason TEXT,
                source TEXT,
                status TEXT NOT NULL,
                response_msg TEXT
            )
            """
        )
        try:
            from src.db.repository import _ensure_column

            _ensure_column(conn, "approvals", "strategy_id", "TEXT")
            _ensure_column(conn, "approvals", "strategy_version", "INTEGER")
            _ensure_column(conn, "approvals", "profile_hash", "TEXT")
            _ensure_column(conn, "approvals", "source_candidate_id", "INTEGER")
        except Exception:
            pass


def daily_loss_halt_triggered(pnl: int, *, runtime: TraderRuntimeContext | None = None) -> bool:
    settings = (runtime or TraderRuntimeContext.capture()).settings
    total_capital = settings.total_capital
    max_daily_loss_pct = settings.max_daily_loss_pct
    if total_capital <= 0:
        return False
    loss_pct = abs(pnl) / total_capital * 100
    return pnl < 0 and loss_pct >= max_daily_loss_pct


def check_daily_loss(pnl: int, *, runtime: TraderRuntimeContext | None = None) -> bool:
    if Path(".runtime/kill_switch.json").exists():
        logger.warning("Kill switch active — 신규 매수 중단, 보유 포지션 방어는 계속")
        return True
    halted = daily_loss_halt_triggered(pnl, runtime=runtime)
    if halted:
        logger.warning(f"일일 손실 한도 초과: {pnl:+,} KRW — 신규 매수 중단, 보유 포지션 방어는 계속")
    return halted


def queue_approval(
    symbol: str,
    name: str,
    action: str,
    qty: int,
    price: int,
    reason: str = "",
    source: str = "trader",
    strategy_id: str | None = None,
    strategy_version: int | None = None,
    profile_hash: str | None = None,
    source_candidate_id: int | None = None,
) -> int:
    from src.strategy_ids import resolve_order_strategy_id

    strategy_id = resolve_order_strategy_id(
        strategy_id,
        source=source,
        reason=reason,
        default="seven_split" if source == "auto_trader" else "",
    ) or None
    from src.application.orders.approval import create_domestic_approval

    return create_domestic_approval(
        connect=connect_db, init_db=init_db, symbol=symbol, name=name,
        action=action, qty=qty, price=price, reason=reason, source=source,
        strategy_id=strategy_id, strategy_version=strategy_version,
        profile_hash=profile_hash, source_candidate_id=source_candidate_id,
    )


def _is_executable_plan_row(row: dict) -> bool:
    return row.get("action") in {"buy", "sell"} and int(row.get("qty", 0) or 0) > 0


def _estimated_buy_cost(row: dict) -> int:
    try:
        explicit_cost = float(row.get("estimated_cost") or 0)
    except (TypeError, ValueError):
        explicit_cost = 0
    if explicit_cost > 0:
        return int(explicit_cost)
    try:
        qty = int(row.get("qty", 0) or 0)
        price = int(row.get("price", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return int(qty * price * 1.001) if qty > 0 and price > 0 else 0


def execute_plan_row(api, context: dict, row: dict) -> dict:
    if not _is_executable_plan_row(row):
        return {**row, "decision": "skip", "ok": True}

    mode = context.get("mode")
    if mode == "analysis_only":
        strategy_id = (
            row.get("strategy_id")
            or (AI_REBALANCE_STRATEGY_ID if row.get("category") == "ai_rebalance" else None)
            or context.get("strategy_id")
            or "seven_split"
        )
        source = "ai-allocation" if row.get("category") == "ai_rebalance" else "auto_trader"
        approval_id = queue_approval(
            row["symbol"],
            row["name"],
            row["action"],
            row["qty"],
            row["price"],
            row.get("reason", ""),
            source=source,
            strategy_id=strategy_id,
            strategy_version=row.get("strategy_version"),
            profile_hash=row.get("profile_hash"),
            source_candidate_id=row.get("source_candidate_id"),
        )
        return {**row, "decision": "queue", "ok": True, "approval_id": approval_id}

    router = context.get("router")
    if router is None:
        return {**row, "decision": "skip", "ok": False}

    strategy_id = (
        row.get("strategy_id")
        or (AI_REBALANCE_STRATEGY_ID if row.get("category") == "ai_rebalance" else None)
        or context.get("strategy_id")
        or "seven_split"
    )
    result = router.route(
        row["symbol"],
        row["name"],
        row["action"],
        row["qty"],
        row["price"],
        row.get("reason", ""),
        row.get("indicators", {}),
        strategy_id=strategy_id,
    )
    ok = result.get("ok", False)
    if result.get("status") == "pending" or "approval_id" in result:
        decision = "queue" if ok else "failed"
    else:
        decision = "execute" if ok else "failed"
    ret = {**row, "decision": decision, "ok": ok}
    if "approval_id" in result:
        ret["approval_id"] = result["approval_id"]
    return ret


def _holding_history_from_balance(api, stocks: list[dict]) -> list[dict]:
    holdings = []
    for stock in stocks:
        qty = int(stock.get("hldg_qty", 0) or 0)
        price = int(stock.get("prpr", 0) or 0)
        value = int(stock.get("evlu_amt", 0) or 0)
        if price <= 0 and qty > 0:
            price = round(value / qty)
        symbol = stock.get("pdno", "")
        daily = api.get_daily(symbol, n=120)
        prices = [float(row["stck_clpr"]) for row in daily if row.get("stck_clpr")]
        highs = [float(row["stck_hgpr"]) for row in daily if row.get("stck_hgpr")]
        volumes = [float(row["acml_vol"]) for row in daily if row.get("acml_vol")]
        prices.reverse()
        highs.reverse()
        volumes.reverse()
        holdings.append({
            "symbol": symbol,
            "name": stock.get("prdt_name", symbol),
            "qty": qty,
            "price": price,
            "value": value if value > 0 else qty * price,
            "prices": prices,
            "highs": highs,
            "volumes": volumes,
        })
    return holdings


def _sell_order_symbols_by_status() -> dict[str, set[str]]:
    statuses = {
        "submitted": set(),
        "open": set(),
        "partial": set(),
        "failed": set(),
    }
    try:
        with connect_db() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT symbol, COALESCE(order_status, '') AS order_status, COALESCE(broker_order_id, '') AS broker_order_id
                FROM trades
                WHERE action = 'sell'
                  AND COALESCE(order_status, '') IN ('submitted', 'partial', 'open', 'failed')
                  AND COALESCE(symbol, '') != ''
                """
            ).fetchall()
        for row in rows:
            symbol = str(row["symbol"] if hasattr(row, "keys") else row[0])
            status = str(row["order_status"] if hasattr(row, "keys") else row[1])
            broker_order_id = str(row["broker_order_id"] if hasattr(row, "keys") else row[2])
            if status in {"submitted", "open"} and not broker_order_id:
                continue
            if status in statuses:
                statuses[status].add(symbol)
    except Exception as exc:
        logger.warning(f"Failed to load unresolved sell order symbols: {exc}")
    return statuses


def _open_sell_order_symbols() -> set[str]:
    by_status = _sell_order_symbols_by_status()
    return by_status["submitted"] | by_status["open"] | by_status["partial"]


def _holding_qty(stock: dict, key: str) -> int:
    try:
        return int(stock.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _attach_holding_snapshots(
    plan: list[dict],
    stocks: list[dict],
    market_data_api=None,
) -> list[dict]:
    """Attach account values without changing order quantity or order price."""
    holdings_by_symbol = {
        str(stock.get("pdno") or ""): stock
        for stock in stocks
        if stock.get("pdno")
    }
    enriched_plan = []
    for item in plan:
        row = dict(item)
        holding = holdings_by_symbol.get(str(row.get("symbol") or ""))
        holding_qty = _holding_qty(holding, "hldg_qty") if holding else 0
        if holding and holding_qty > 0:
            current_price = _holding_qty(holding, "prpr")
            if current_price <= 0 and holding_qty > 0:
                evaluation_amount = _holding_qty(holding, "evlu_amt")
                if evaluation_amount > 0:
                    current_price = round(evaluation_amount / holding_qty)
            if current_price <= 0:
                current_price = _holding_qty(row, "price")
            if current_price <= 0 and market_data_api is not None:
                try:
                    quote = market_data_api.get_quote(str(row.get("symbol") or "")) or {}
                    current_price = _holding_qty(quote, "current")
                except Exception as exc:
                    logger.warning(
                        "Holding quote fallback failed symbol={}: {}",
                        row.get("symbol"),
                        exc,
                    )
            row["holding_qty"] = holding_qty
            row["current_price"] = current_price
        enriched_plan.append(row)
    return enriched_plan


def build_ai_rebalance_rows(api, balance_data: dict, total_eval: int) -> list[dict]:
    stocks = balance_data.get("output1", [])
    holdings = _holding_history_from_balance(api, stocks)
    ai_plan = generate_ai_weight_plan(holdings, total_eval)
    rows = []
    for position in ai_plan.get("positions", []):
        if is_excluded_symbol(position.get("symbol", "")):
            logger.info(f"[EXCLUDE] Skipping AI rebalance for excluded symbol {position.get('symbol', '')}")
            continue
        action = position.get("rebalance_action", "hold")
        qty = int(position.get("rebalance_qty", 0) or 0)
        if action not in {"buy", "sell"} or qty <= 0:
            continue
        target_weight = float(position.get("target_weight", 0) or 0)
        current_weight = float(position.get("current_weight", 0) or 0)
        reason = (
            f"AI rebalance {current_weight * 100:.1f}% -> {target_weight * 100:.1f}%"
        )
        if position.get("reasoning_kr"):
            reason = f"{reason} | {position['reasoning_kr']}"
        rows.append(PlanRow(
            symbol=str(position.get("symbol", "")),
            name=str(position.get("name") or position.get("symbol", "")),
            action=action,
            qty=qty,
            price=int(position.get("price", 0) or 0),
            reason=reason,
            source="ai_rebalance",
            category="ai_rebalance",
            score=position.get("score"),
            reasons=list(position.get("reasons") or []),
            metadata={
                "target_weight": target_weight,
                "current_weight": current_weight,
                "target_value": position.get("target_value", 0),
                "delta_value": position.get("delta_value", 0),
                "ai_active": bool(ai_plan.get("ai_active")),
            },
            strategy_id=AI_REBALANCE_STRATEGY_ID,
        ).to_dict())
    return rows


def apply_market_regime_sizing(
    plan: list[dict],
    *,
    multiplier: float,
    block_reason: str | None = None,
) -> list[dict]:
    """Scale only new-risk rows; reductions and exits remain untouched."""
    normalized = max(0.0, min(1.0, float(multiplier)))
    for row in plan:
        if str(row.get("action") or "").lower() != "buy":
            continue
        original_qty = int(float(row.get("qty") or 0))
        scaled_qty = int(original_qty * normalized)
        row["qty"] = scaled_qty
        if row.get("estimated_cost") is not None and original_qty > 0:
            row["estimated_cost"] = float(row["estimated_cost"]) * scaled_qty / original_qty
        row.setdefault("metadata", {})["market_regime_sizing"] = {
            "original_qty": original_qty,
            "multiplier": normalized,
            "scaled_qty": scaled_qty,
            "block_reason": block_reason,
        }
    return plan


def build_runtime_plan(
    api,
    balance_data: dict,
    *,
    include_ai_rebalance: bool = False,
    read_cached_candidates: bool = False,
    force_strategy_id: str | None = None,
    candidate_scan_override: dict | None = None,
    runtime: TraderRuntimeContext | None = None,
    new_risk_multiplier: float = 1.0,
    new_risk_block_reason: str | None = None,
    market_regime_policy: dict | None = None,
) -> dict:
    api_runtime = getattr(api, "runtime", None)
    if runtime is None and isinstance(api_runtime, TraderRuntimeContext):
        runtime = api_runtime
    runtime = runtime or TraderRuntimeContext.capture()
    active_strategy_id = force_strategy_id
    active_strategy = None
    try:
        from src.db.repository import load_ai_strategies
        strategies = load_ai_strategies()
        if active_strategy_id:
            active_strategy = next((s for s in strategies if s.get("id") == active_strategy_id or s.get("model") == active_strategy_id), None)
        else:
            active_strategy = next((s for s in strategies if s.get("selected")), None)
            if active_strategy:
                active_strategy_id = active_strategy.get("id") or "seven_split"
            else:
                active_strategy_id = "seven_split"
    except Exception:
        if not active_strategy_id:
            active_strategy_id = "seven_split"

    stocks = balance_data.get("output1", [])
    from src.strategy.position_tracker import clear_missing_positions

    clear_missing_positions(
        "KR",
        {
            str(stock.get("pdno") or "")
            for stock in stocks
            if _holding_qty(stock, "hldg_qty") > 0 and stock.get("pdno")
        },
    )
    summary = (balance_data.get("output2") or [{}])[0]
    cash = int(summary.get("prvs_rcdl_excc_amt", 0) or 0)
    if cash == 0:
        cash = int(summary.get("dnca_tot_amt", 0) or 0)
    if cash == 0:
        summary_total = int(summary.get("tot_evlu_amt", 0) or 0)
        summary_stock_eval = int(summary.get("scts_evlu_amt", 0) or 0)
        if summary_total > 0:
            cash = summary_total - summary_stock_eval
    total_eval = int(summary.get("tot_evlu_amt", 0) or 0)
    stock_eval = int(summary.get("scts_evlu_amt", 0) or 0)
    if stock_eval <= 0:
        stock_eval = sum(
            int(stock.get("evlu_amt", 0) or 0)
            for stock in stocks
        )
    capital = operating_capital(total_eval, runtime=runtime)
    sell_order_symbols = _sell_order_symbols_by_status()
    active_sell_symbols = sell_order_symbols["submitted"] | sell_order_symbols["open"]
    retryable_sell_symbols = sell_order_symbols["partial"] | sell_order_symbols["failed"]
    locked_holding_symbols = {
        str(stock.get("pdno", ""))
        for stock in stocks
        if _holding_qty(stock, "hldg_qty") > 0
        and (
            str(stock.get("pdno", "")) in active_sell_symbols
            or ("ord_psbl_qty" in stock and _holding_qty(stock, "ord_psbl_qty") <= 0)
        )
    }
    locked_holding_eval = sum(
        int(stock.get("evlu_amt", 0) or 0)
        for stock in stocks
        if str(stock.get("pdno", "")) in locked_holding_symbols
    )
    buying_cash_info = buying_cash_diagnostics(
        cash,
        stock_eval,
        total_eval,
        locked_holding_eval=locked_holding_eval,
        runtime=runtime,
    )
    buying_cash = int(buying_cash_info["buying_cash"])
    pnl = int(summary.get("evlu_pfls_smtl_amt", 0) or 0)
    isolated_strategy_run = active_strategy_id in _ISOLATED_STRATEGY_IDS
    owned_symbols = set()
    owned_position_qty = {}
    if active_strategy_id:
        try:
            from src.db.repository import reconstruct_strategy_positions
            reconstructed = reconstruct_strategy_positions(
                active_strategy_id, runtime.flags.trading_env
            )
            owned_position_qty = {
                str(item.get("symbol") or ""): int(item.get("qty") or 0)
                for item in reconstructed
                if int(item.get("qty") or 0) > 0
            }
            owned_symbols = set(owned_position_qty)
            if active_strategy_id in _ISOLATED_STRATEGY_IDS:
                from src.strategy.position_tracker import clear_missing_strategy_positions
                clear_missing_strategy_positions("KR", active_strategy_id, owned_symbols)
        except Exception as ownership_error:
            logger.warning(f"[OWNERSHIP] strategy position lookup failed: {ownership_error}")

    position_rows = []
    if stocks and (
        not isolated_strategy_run
        or active_strategy_id == "heikin_ashi_scalping_strategy"
    ):
        for stock in stocks:
            sym = stock.get("pdno", "")
            if is_excluded_symbol(sym):
                logger.info(f"[EXCLUDE] Skipping holding signal for excluded symbol {sym}")
                continue
            name = stock.get("prdt_name", sym)
            if active_strategy_id in {
                "rsi_limit_strategy", "heikin_ashi_scalping_strategy"
            } and sym not in owned_symbols:
                logger.info(
                    f"[OWNERSHIP] {sym} skipped: not owned by {active_strategy_id}"
                )
                continue
            if sym in locked_holding_symbols:
                row = signal_to_plan_row(
                    sym,
                    name,
                    {
                        "action": "hold",
                        "qty": 0,
                        "price": int(stock.get("prpr", 0) or 0),
                        "reason": "sell order pending or holding is not orderable",
                        "indicators": {},
                    },
                    source="locked_holding",
                    include_hold=True,
                    metadata={
                        "locked_holding": True,
                        "ord_psbl_qty": _holding_qty(stock, "ord_psbl_qty"),
                    },
                    strategy_id=active_strategy_id,
                )
                if row is not None:
                    row["skip_reason"] = "sell order pending or holding is not orderable"
                    position_rows.append(row)
                continue
            if sym in retryable_sell_symbols:
                sellable_qty = min(
                    _holding_qty(stock, "hldg_qty"),
                    _holding_qty(stock, "ord_psbl_qty"),
                )
                if sellable_qty > 0:
                    position_rows.append(PlanRow(
                        symbol=str(sym),
                        name=str(name),
                        action="sell",
                        qty=sellable_qty,
                        price=0,
                        reason="retry unresolved sell order after partial/failed execution",
                        source="sell_retry",
                        category="position",
                        metadata={
                            "sell_retry": True,
                            "ord_psbl_qty": _holding_qty(stock, "ord_psbl_qty"),
                        },
                        strategy_id=active_strategy_id,
                    ).to_dict())
                    continue
            rt = float(stock.get("evlu_pfls_rt", 0) or 0)
            strategy_model = ""
            if active_strategy:
                strategy_model = str(active_strategy.get("model") or "")
                if strategy_model == "none":
                    strategy_model = ""
            daily_count = 750 if strategy_model in {
                "rsi_limit_strategy", "heikin_ashi_scalping_strategy"
            } else 60
            daily = api.get_daily(sym, n=daily_count)
            signal_stock = stock
            if active_strategy_id in _ISOLATED_STRATEGY_IDS:
                broker_qty = _holding_qty(stock, "hldg_qty")
                orderable_qty = _holding_qty(stock, "ord_psbl_qty")
                owned_qty = min(broker_qty, owned_position_qty.get(str(sym), 0))
                signal_stock = dict(stock)
                signal_stock["hldg_qty"] = owned_qty
                signal_stock["ord_psbl_qty"] = min(orderable_qty, owned_qty)
            signal = generate_signal(signal_stock, daily, strategy_model=strategy_model)
            row = signal_to_plan_row(
                sym,
                name,
                signal,
                source="holding_signal",
                include_hold=True,
                metadata={"return_pct": rt},
                strategy_id=active_strategy_id,
            )
            if row is not None:
                position_rows.append(row)

    halted = daily_loss_halt_triggered(pnl, runtime=runtime)
    remaining_cash = buying_cash
    candidate_rows = []
    candidate_scan: dict = {"candidates": [], "scan_summary": [], "scanned": 0, "min_score": 2, "scan_error": None}

    if not halted:
        held_symbols = {s.get("pdno", "") for s in stocks}
        
        if candidate_scan_override is not None:
            candidates = list(candidate_scan_override.get("candidates") or [])
        elif read_cached_candidates:
            from src.db.repository import get_latest_scanned_candidates
            db_candidates = get_latest_scanned_candidates(active_strategy_id)
            candidates = []
            for row in db_candidates:
                candidates.append({
                    "ticker": row["symbol"],
                    "name": row["name"],
                    "current_price": row["price"],
                    "score": row["score"],
                    "rule_score": row["rule_score"] if row["rule_score"] is not None else row["score"],
                    "ml_score": row["ml_score"],
                    "final_score": row["final_score"] if row["final_score"] is not None else row["score"],
                    "reasons": row["reasons"].split(",") if row["reasons"] else [],
                    "rsi": row["rsi"],
                    "rsi2": row["rsi2"],
                    "macd_hist": row["macd_hist"],
                    "sma20": row["sma20"],
                    "sma60": row["sma60"],
                    "bb_lo": row.get("bb_lo") or 0.0,
                    "bb_hi": row.get("bb_hi") or 0.0,
                })
            candidates = sorted(candidates, key=lambda c: (-float(c["final_score"]), c["ticker"]))
        else:
            # Isolated strategies must use their own universe only. If the
            # universe is empty, do not fall back to the shared Hanstock list.
            strategy_universe_missing = False
            universe = []
            if active_strategy_id:
                try:
                    from src.db.repository import load_strategy_universe_symbols, load_watchlist_data
                    dedicated = load_strategy_universe_symbols(active_strategy_id)
                    registered = set(load_watchlist_data().get("symbols", []))
                    if dedicated:
                        universe = [
                            code
                            for code in dedicated
                            if code not in held_symbols
                        ]
                    elif active_strategy_id in _ISOLATED_STRATEGY_IDS:
                        universe = []
                        strategy_universe_missing = True
                    else:
                        universe = [
                            code
                            for code in registered
                            if code not in held_symbols
                        ]
                except Exception:
                    if active_strategy_id in _ISOLATED_STRATEGY_IDS:
                        universe = []
                        strategy_universe_missing = True
            if not universe and not strategy_universe_missing and active_strategy_id not in _ISOLATED_STRATEGY_IDS:
                universe = []
            if strategy_universe_missing:
                scan_result = {
                    "candidates": [],
                    "scan_summary": [],
                    "scanned": 0,
                    "min_score": 1.0 if active_strategy_id == "plunge_bounce_strategy" else 2,
                    "scan_error": (
                        f"{active_strategy_id} has no dedicated universe. "
                        "Register strategy-specific watchlist symbols first."
                    ),
                }
                candidates = []
            elif active_strategy_id == "plunge_bounce_strategy":
                scan_result = find_candidates(
                    held_symbols,
                    universe=universe,
                    min_score=1.0,
                    ranker="rule_only",
                    api=api,
                    strategy_model="plunge_bounce_strategy",
                )
            else:
                ranker = "gpt_5_mini"
                strategy_model = ""
                strategy_profile = None
                strategy_description = ""
                if active_strategy:
                    model = active_strategy.get("model") or "none"
                    provider = active_strategy.get("provider") or "none"
                    profile = active_strategy.get("profile") or {}
                    weight = float(profile.get("ai_weight", active_strategy.get("weight", 0.0)) or 0.0)

                    strategy_model = model
                    strategy_profile = profile
                    strategy_description = active_strategy.get("description") or ""
                    if provider == "none" or model == "none" or weight == 0.0:
                        ranker = "rule_only"
                    else:
                        ranker = model
                scan_result = find_candidates(
                    held_symbols,
                    universe=universe,
                    ranker=ranker,
                    strategy_model=strategy_model,
                    strategy_profile=strategy_profile,
                    strategy_description=strategy_description,
                    api=api,
                )
            candidates = scan_result.get("candidates", [])

        orders = build_orders(candidates, api.get_quote, len(held_symbols), buying_cash)
        order_by_ticker = {o["ticker"]: o for o in orders}

        for candidate in candidates:
            order = order_by_ticker.get(candidate["ticker"], {})
            row = candidate_order_to_plan_row(candidate, order, source="candidate_order", strategy_id=active_strategy_id)
            indicators = {
                k: v
                for k, v in candidate.items()
                if k in _CANDIDATE_INDICATOR_KEYS and v is not None
            }
            row = {**row, "indicators": indicators}
            candidate_rows.append(row)
            
            # Automatically save scan results to DB for history tracking in automated cycles
            if not read_cached_candidates:
                from src.db.repository import save_scanned_candidate
                save_scanned_candidate(
                    symbol=candidate.get("ticker", candidate.get("symbol", "")),
                    name=candidate.get("name", candidate.get("ticker", "")),
                    score=candidate.get("score", 0),
                    reasons=candidate.get("reasons", []),
                    price=candidate.get("current_price", candidate.get("price", 0)),
                    env=runtime.flags.trading_env,
                    indicators=indicators
                )
            if order:
                remaining_cash -= int(order.get("estimated_cost", 0) or 0)

        if candidate_scan_override is not None:
            candidate_scan = {
                "candidates": candidates,
                "scan_summary": list(candidate_scan_override.get("scan_summary") or []),
                "scanned": int(candidate_scan_override.get("scanned") or len(candidates)),
                "min_score": candidate_scan_override.get("min_score", 2),
                "scan_error": candidate_scan_override.get("scan_error"),
            }
        elif read_cached_candidates:
            candidate_scan = {
                "candidates": candidates,
                "scan_summary": candidates,
                "scanned": len(candidates),
                "min_score": 2,
                "scan_error": None if candidates else "No cached candidates found in database",
            }
        else:
            candidate_scan = {
                "candidates": candidates,
                "scan_summary": scan_result.get("scan_summary", []),
                "scanned": scan_result.get("scanned", 0),
                "min_score": scan_result.get("min_score", 2),
                "scan_error": scan_result.get("scan_error"),
            }

            # AI 자동 추가적용 로직 (스케줄러 주기적 관리 지원)
            if not halted:
                from src.db.repository import load_watchlist_data, save_watchlist_data
                from src.strategy.seven_split import sync_watchlist_runtime
                try:
                    watchlist_data = load_watchlist_data()
                    if watchlist_data.get("ai_auto_add", False):
                        threshold = float(watchlist_data.get("ai_auto_add_threshold", 3.0))
                        symbols = list(watchlist_data.get("symbols", []))
                        symbol_set = set(symbols)
                        
                        score_by_symbol = {}
                        name_by_symbol = {}
                        for row in scan_result.get("scan_summary", []) or []:
                            sym = row.get("ticker") or row.get("symbol")
                            if sym:
                                score_by_symbol[str(sym)] = float(row.get("score", 0.0) or 0.0)
                                if row.get("name"):
                                    name_by_symbol[str(sym)] = row["name"]
                                    
                        changed = False
                        for cand in candidates:
                            score = float(cand.get("score", 0.0) or 0.0)
                            from src.strategy.seven_split import KOSPI_UNIVERSE
                            from src.strategy.watchlist_policy import eligibility_reason

                            rejection = eligibility_reason(
                                price=cand.get("current_price") or cand.get("price"),
                                market_cap=cand.get("market_cap"),
                                known_mid_large=str(cand.get("ticker") or cand.get("symbol") or "") in KOSPI_UNIVERSE,
                            )
                            if score >= threshold and not rejection:
                                sym = str(cand["ticker"])
                                name_by_symbol.setdefault(sym, cand.get("name") or sym)
                                if sym not in symbol_set:
                                    symbols.append(sym)
                                    symbol_set.add(sym)
                                    changed = True
                                    logger.info(f"[WATCHLIST AUTO-ADD] Added {sym} (score={score})")
                                    
                        if changed:
                            # ai_auto_add is intentionally additive. A score below the
                            # entry threshold only means that the symbol has no signal
                            # in this scan; it is not evidence that an explicitly
                            # registered watchlist item should be deleted.
                            watchlist_data["symbols"] = symbols
                            save_watchlist_data(watchlist_data)
                            sync_watchlist_runtime()
                except Exception as w_err:
                    logger.warning(f"Failed to auto-add high score candidate to watchlist in cycle: {w_err}")

    plan = build_execution_plan(position_rows=position_rows, candidate_rows=candidate_rows)
    ai_rebalance_rows = []
    if include_ai_rebalance and not halted:
        ai_rebalance_rows = build_ai_rebalance_rows(api, balance_data, capital)
        plan.extend(ai_rebalance_rows)

    apply_market_regime_sizing(
        plan,
        multiplier=new_risk_multiplier,
        block_reason=new_risk_block_reason,
    )

    return {
        "plan": plan,
        "position_plan_rows": position_rows,
        "candidate_plan_rows": candidate_rows,
        "ai_rebalance_rows": ai_rebalance_rows,
        "remaining_cash": remaining_cash,
        "daily_loss_halt": halted,
        "candidate_scan": candidate_scan,
        "cash": cash,
        "buying_cash": buying_cash,
        "buying_cash_info": buying_cash_info,
        "operating_capital": capital,
        "locked_holding_symbols": sorted(locked_holding_symbols),
        "retryable_sell_symbols": sorted(retryable_sell_symbols),
        "held_symbols": {s.get("pdno", "") for s in stocks},
        "market_regime_policy": dict(market_regime_policy or {}),
    }


def run(
    mode: str | None = None,
    *,
    include_ai_rebalance: bool = False,
    execution_categories: set[str] | None = None,
    force_strategy_id: str | None = None,
    runtime: TraderRuntimeContext | None = None,
    new_risk_multiplier: float = 1.0,
    new_risk_block_reason: str | None = None,
    market_regime_policy: dict | None = None,
) -> dict:
    runtime = runtime or TraderRuntimeContext.capture()
    settings = runtime.settings
    flags = runtime.flags
    check_secrets()
    init_db()
    init_approval_db()

    from src.broker.factory import create_domestic_stock_broker
    api = create_domestic_stock_broker(
        broker=settings.domestic_stock_broker,
        settings=settings,
        order_submission_enabled=flags.order_submission_enabled,
    )
    market_data_api = build_market_data_api(api)
    account = api.fetch_balance()
    if not isinstance(account, AccountBalance):
        raw_balance = api.get_balance()
        rows = raw_balance.get("output1", [])
        summary = (raw_balance.get("output2") or [{}])[0]
        account = AccountBalance(
            holdings=tuple(Holding(
                symbol=str(row.get("pdno") or ""),
                name=str(row.get("prdt_name") or ""),
                quantity=int(float(row.get("hldg_qty") or 0)),
                sellable_quantity=int(float(row.get("ord_psbl_qty") or 0)),
                average_price=float(row.get("pchs_avg_pric") or 0),
                current_price=float(row.get("prpr") or 0),
                market_value=float(row.get("evlu_amt") or 0),
                profit_loss=float(row.get("evlu_pfls_amt") or 0),
                profit_loss_rate=float(row.get("evlu_pfls_rt") or 0),
            ) for row in rows),
            cash=float(summary.get("dnca_tot_amt") or summary.get("prvs_rcdl_excc_amt") or 0),
            total_equity=float(summary.get("tot_evlu_amt") or 0),
            stock_value=float(summary.get("scts_evlu_amt") or 0),
            profit_loss=float(summary.get("evlu_pfls_smtl_amt") or 0),
        )
    balance = {
        "output1": [{
            "pdno": row.symbol,
            "prdt_name": row.name,
            "hldg_qty": str(row.quantity),
            "ord_psbl_qty": str(row.sellable_quantity),
            "pchs_avg_pric": str(int(round(row.average_price))),
            "prpr": str(int(round(row.current_price))),
            "evlu_amt": str(int(round(row.market_value))),
            "evlu_pfls_amt": str(int(round(row.profit_loss))),
            "evlu_pfls_rt": str(row.profit_loss_rate),
        } for row in account.holdings],
        "output2": [{
            "prvs_rcdl_excc_amt": str(int(round(account.cash))),
            "dnca_tot_amt": str(int(round(account.cash))),
            "tot_evlu_amt": str(int(round(account.total_equity))),
            "scts_evlu_amt": str(int(round(account.stock_value))),
            "evlu_pfls_smtl_amt": str(int(round(account.profit_loss))),
        }],
    }

    stocks = balance.get("output1", [])
    summary = (balance.get("output2") or [{}])[0]
    cash = int(summary.get("prvs_rcdl_excc_amt", 0) or 0)
    if cash == 0:
        cash = int(summary.get("dnca_tot_amt", 0) or 0)
    if cash == 0:
        summary_total = int(summary.get("tot_evlu_amt", 0) or 0)
        summary_stock_eval = int(summary.get("scts_evlu_amt", 0) or 0)
        if summary_total > 0:
            cash = summary_total - summary_stock_eval
    total_eval = int(summary.get("tot_evlu_amt", 0) or 0)
    pnl = int(summary.get("evlu_pfls_smtl_amt", 0) or 0)

    logger.info("=" * 60)
    logger.info(
        "Seven Split started | "
        f"DRY_RUN={flags.dry_run} | "
        f"ENABLE_LIVE_TRADING={flags.enable_live_trading} | "
        f"ENV={flags.trading_env}"
    )
    logger.info(
        f"Order submission enabled: {flags.order_submission_enabled} | "
        f"Real orders enabled: {flags.real_orders_enabled}"
    )
    logger.info(f"Cash={cash:,} KRW | Total={total_eval:,} KRW | PnL={pnl:+,} KRW | Holdings={len(stocks)}")

    notify_session = mode != "analysis_only"
    if notify_session:
        slack_session_start(
            cash=cash,
            total=total_eval,
            stock_count=len(stocks),
            order_submission_enabled=flags.order_submission_enabled,
            real_orders_enabled=flags.real_orders_enabled,
        )

    daily_loss_halted = check_daily_loss(pnl, runtime=runtime)

    bp_kwargs = {}
    if include_ai_rebalance:
        bp_kwargs["include_ai_rebalance"] = True
    if force_strategy_id is not None:
        bp_kwargs["force_strategy_id"] = force_strategy_id

    runtime_bundle = build_runtime_plan(
        market_data_api,
        balance,
        new_risk_multiplier=new_risk_multiplier,
        new_risk_block_reason=new_risk_block_reason,
        market_regime_policy=market_regime_policy,
        **bp_kwargs,
    )
    runtime_bundle["plan"] = _attach_holding_snapshots(
        runtime_bundle["plan"],
        stocks,
        market_data_api,
    )
    daily_loss_halted = daily_loss_halted or bool(runtime_bundle.get("daily_loss_halt"))

    candidates = runtime_bundle.get("candidate_scan", {}).get("candidates", [])
    if candidates and notify_session:
        slack_candidates(candidates)

    context: dict = {"mode": mode, "strategy_id": force_strategy_id or "seven_split"}
    if mode != "analysis_only":
        context["router"] = OrderRouter(api, execution_context=runtime.flags)

    results = []
    execution_buying_cash = int(runtime_bundle.get("buying_cash", cash) or 0)
    for row in runtime_bundle["plan"]:
        if execution_categories is not None and row.get("category") not in execution_categories:
            results.append({**row, "decision": "skip", "ok": True, "skip_reason": "category filtered"})
            continue
        if row.get("action") == "buy" and (
            new_risk_block_reason or int(float(row.get("qty") or 0)) <= 0
        ):
            skip_reason = (
                new_risk_block_reason
                or row.get("skip_reason")
                or "market regime sizing below one share"
            )
            results.append({
                **row,
                "decision": "skip",
                "ok": True,
                "skip_reason": skip_reason,
            })
            continue
        if daily_loss_halted and row.get("action") == "buy":
            results.append({
                **row,
                "decision": "skip",
                "ok": True,
                "skip_reason": "daily loss halt blocks buy orders only",
            })
            continue
        if row.get("action") == "buy":
            estimated_cost = _estimated_buy_cost(row)
            if execution_buying_cash <= 0:
                buying_cash_info = runtime_bundle.get("buying_cash_info") or {}
                skip_reason = "buying cash unavailable"
                broker_cash = int(buying_cash_info.get("broker_cash") or cash or 0)
                exposure_remaining = int(buying_cash_info.get("exposure_remaining") or 0)
                if broker_cash > 0 and exposure_remaining <= 0:
                    skip_reason = "capital exposure limit reached"
                logger.warning(
                    f"[BUY SKIP] {skip_reason}: "
                    f"{row.get('symbol')} qty={row.get('qty')} price={row.get('price')} "
                    f"cash={broker_cash:,} "
                    f"stock_eval={int(buying_cash_info.get('stock_eval') or 0):,} "
                    f"investable_limit={int(buying_cash_info.get('investable_limit') or 0):,}"
                )
                results.append({
                    **row,
                    "decision": "skip",
                    "ok": True,
                    "skip_reason": skip_reason,
                })
                continue
            if estimated_cost > execution_buying_cash:
                logger.warning(
                    "[BUY SKIP] buy order exceeds buying cash: "
                    f"{row.get('symbol')} cost={estimated_cost:,} buying_cash={execution_buying_cash:,}"
                )
                results.append({
                    **row,
                    "decision": "skip",
                    "ok": True,
                    "skip_reason": "buy order exceeds buying cash",
                })
                continue
        result_row = execute_plan_row(api, context, row)
        results.append(result_row)
        if row.get("action") == "buy" and result_row.get("ok"):
            execution_buying_cash = max(0, execution_buying_cash - _estimated_buy_cost(row))

    remaining_cash = runtime_bundle.get("remaining_cash", cash)
    if notify_session:
        slack_session_end(results=results, cash=remaining_cash, total=total_eval, pnl=pnl)

    logger.info("Seven Split finished")
    return {
        "plan": runtime_bundle["plan"],
        "results": results,
        **{k: v for k, v in runtime_bundle.items() if k != "plan"},
    }


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.exception("Critical error in trader:")
        if hasattr(e, "last_attempt") and e.last_attempt.exception():
            original_err = e.last_attempt.exception()
            slack_error(f"실행 중 치명적인 오류가 발생했습니다: {original_err}")
        else:
            slack_error(f"실행 중 치명적인 오류가 발생했습니다: {e}")
        raise
