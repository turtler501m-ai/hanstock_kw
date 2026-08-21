"""Persistent entry-aware peak tracking for trailing stops."""

from __future__ import annotations

from src.runtime_state import runtime_state_store


STATE_KEY = "technical_position_peaks_v1"
STRATEGY_RISK_STATE_KEY = "strategy_position_risk_v1"
STRATEGY_REENTRY_STATE_KEY = "strategy_reentry_guard_v1"


def update_position_peak(
    market: str,
    symbol: str,
    *,
    current_price: float,
    entry_price: float,
    quantity: float,
) -> dict:
    normalized_symbol = str(symbol or "").upper().strip()
    if not normalized_symbol:
        return {
            "market": str(market).upper(),
            "symbol": "",
            "entry_price": round(max(0.0, float(entry_price or 0)), 6),
            "quantity": round(max(0.0, float(quantity or 0)), 8),
            "peak_price": round(max(0.0, float(current_price or 0)), 6),
        }
    key = f"{str(market).upper()}:{normalized_symbol}"
    state = runtime_state_store.get(STATE_KEY, {"positions": {}})
    positions = state.setdefault("positions", {})
    current = max(0.0, float(current_price or 0))
    entry = max(0.0, float(entry_price or 0))
    qty = max(0.0, float(quantity or 0))
    if qty <= 0 or current <= 0:
        positions.pop(key, None)
        runtime_state_store.set(STATE_KEY, state)
        return {}

    previous = positions.get(key) or {}
    previous_entry = float(previous.get("entry_price") or 0)
    previous_qty = float(previous.get("quantity") or 0)
    position_changed = (
        previous_entry <= 0
        or abs(entry - previous_entry) / previous_entry > 0.005
        or qty > previous_qty + 1e-9
    )
    peak = current if position_changed else max(current, float(previous.get("peak_price") or current))
    row = {
        "market": str(market).upper(),
        "symbol": str(symbol).upper(),
        "entry_price": round(entry, 6),
        "quantity": round(qty, 8),
        "initial_quantity": round(
            qty if position_changed else float(previous.get("initial_quantity") or previous_qty or qty),
            8,
        ),
        "peak_price": round(peak, 6),
    }
    positions[key] = row
    runtime_state_store.set(STATE_KEY, state)
    return row


def clear_missing_positions(market: str, active_symbols: set[str]) -> None:
    prefix = f"{str(market).upper()}:"
    active = {str(symbol).upper() for symbol in active_symbols}
    state = runtime_state_store.get(STATE_KEY, {"positions": {}})
    positions = state.setdefault("positions", {})
    for key in list(positions):
        if key.startswith(prefix) and key[len(prefix):] not in active:
            positions.pop(key, None)
    runtime_state_store.set(STATE_KEY, state)


def update_strategy_position_risk(
    market: str,
    symbol: str,
    strategy_id: str,
    *,
    entry_price: float,
    quantity: float,
    proposed_stop: float,
    evaluation_key: str = "",
) -> dict:
    """Persist immutable initial R and a stop that can only tighten for longs."""
    key = f"{str(market).upper()}:{str(symbol).upper()}:{strategy_id}"
    state = runtime_state_store.get(STRATEGY_RISK_STATE_KEY, {"positions": {}})
    positions = state.setdefault("positions", {})
    qty = max(0.0, float(quantity or 0))
    entry = max(0.0, float(entry_price or 0))
    candidate_stop = max(0.0, float(proposed_stop or 0))
    if qty <= 0 or entry <= 0:
        positions.pop(key, None)
        runtime_state_store.set(STRATEGY_RISK_STATE_KEY, state)
        return {}
    previous = positions.get(key) or {}
    previous_entry = float(previous.get("entry_price") or 0)
    new_episode = previous_entry <= 0 or abs(entry - previous_entry) / previous_entry > 0.005 or qty > float(previous.get("initial_quantity") or 0)
    if new_episode:
        initial_stop = min(candidate_stop, entry * 0.999)
        row = {
            "market": str(market).upper(), "symbol": str(symbol).upper(),
            "strategy_id": strategy_id, "entry_price": entry,
            "initial_quantity": qty, "initial_stop": initial_stop,
            "current_stop": initial_stop, "initial_r": entry - initial_stop,
            "opened_evaluation_key": evaluation_key,
            "last_evaluation_key": evaluation_key,
            "holding_bars": 0,
        }
    else:
        row = dict(previous)
        # A long stop is never allowed to move down after entry.
        row["current_stop"] = max(float(previous.get("current_stop") or 0), candidate_stop)
        row["quantity"] = qty
        if evaluation_key and evaluation_key != previous.get("last_evaluation_key"):
            row["holding_bars"] = int(previous.get("holding_bars") or 0) + 1
            row["last_evaluation_key"] = evaluation_key
    positions[key] = row
    runtime_state_store.set(STRATEGY_RISK_STATE_KEY, state)
    return row


def require_new_oversold_episode(market: str, symbol: str, strategy_id: str) -> None:
    key = f"{str(market).upper()}:{str(symbol).upper()}:{strategy_id}"
    state = runtime_state_store.get(STRATEGY_REENTRY_STATE_KEY, {"guards": {}})
    state.setdefault("guards", {})[key] = {"reset_required": True}
    runtime_state_store.set(STRATEGY_REENTRY_STATE_KEY, state)


def allow_reentry_after_rsi_reset(
    market: str, symbol: str, strategy_id: str, *, current_rsi: float,
) -> bool:
    """Require RSI >= 50 after a stopped episode before another entry."""
    key = f"{str(market).upper()}:{str(symbol).upper()}:{strategy_id}"
    state = runtime_state_store.get(STRATEGY_REENTRY_STATE_KEY, {"guards": {}})
    guards = state.setdefault("guards", {})
    guard = guards.get(key)
    if not guard:
        return True
    if float(current_rsi) >= 50:
        guards.pop(key, None)
        runtime_state_store.set(STRATEGY_REENTRY_STATE_KEY, state)
        return True
    return False


def strategy_open_risk(strategy_id: str) -> float:
    state = runtime_state_store.get(STRATEGY_RISK_STATE_KEY, {"positions": {}})
    return sum(
        max(0.0, float(row.get("initial_r") or 0) * float(row.get("quantity") or row.get("initial_quantity") or 0))
        for row in state.get("positions", {}).values()
        if row.get("strategy_id") == strategy_id
    )


def clear_missing_strategy_positions(
    market: str, strategy_id: str, active_symbols: set[str],
) -> None:
    """Drop risk rows only after filled-trade reconstruction confirms closure."""
    prefix = f"{str(market).upper()}:"
    suffix = f":{strategy_id}"
    active = {str(symbol).upper() for symbol in active_symbols}
    state = runtime_state_store.get(STRATEGY_RISK_STATE_KEY, {"positions": {}})
    positions = state.setdefault("positions", {})
    for key, row in list(positions.items()):
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        if str(row.get("symbol") or "").upper() not in active:
            positions.pop(key, None)
    runtime_state_store.set(STRATEGY_RISK_STATE_KEY, state)
