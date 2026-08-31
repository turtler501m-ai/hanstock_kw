"""Fail-closed startup recovery state for order submission."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.application.orders.health import build_order_health


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_runtime_state(connect, state: str, *, reason: str = "", details=None) -> dict:
    if state not in {"recovering", "reduce_only", "ready"}:
        raise ValueError(f"invalid order runtime state: {state}")
    payload = json.dumps(details or {}, ensure_ascii=False)
    updated_at = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO order_runtime_state(singleton_id,state,reason,details_json,updated_at)
               VALUES(1,?,?,?,?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 state=excluded.state, reason=excluded.reason,
                 details_json=excluded.details_json, updated_at=excluded.updated_at""",
            (state, reason, payload, updated_at),
        )
    return {"state": state, "reason": reason, "details": details or {}, "updated_at": updated_at}


def get_runtime_state(connect) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT state,reason,details_json,updated_at FROM order_runtime_state WHERE singleton_id=1"
        ).fetchone()
    if row is None:
        return {"state": "recovering", "reason": "startup recovery has not completed", "details": {}, "updated_at": None}
    try:
        details = json.loads(row[2] or "{}")
    except (TypeError, ValueError):
        details = {}
    return {"state": row[0], "reason": row[1], "details": details, "updated_at": row[3]}


def close_expired_legacy_day_orders(connect, *, now: datetime | None = None) -> int:
    """Close domestic legacy DAY orders whose KRX order date has ended.

    Imported partial fills remain intact; only the impossible remainder is
    released. Current-session orders and outcome-unknown rows are untouched.
    """
    kst = timezone(timedelta(hours=9))
    current = now or datetime.now(kst)
    cutoff = current.astimezone(kst).strftime("%Y-%m-%d")
    with connect() as conn:
        try:
            cursor = conn.execute(
                """UPDATE trades
                   SET order_status='canceled',
                       response_msg=CASE
                         WHEN COALESCE(response_msg,'')='' THEN
                           'Startup recovery: prior-session DAY order expired'
                         ELSE response_msg || '; startup recovery: prior-session DAY order expired'
                       END
                   WHERE order_status IN ('submitted','open','partial')
                     AND substr(COALESCE(ts,''),1,10) <> ''
                     AND substr(ts,1,10) < ?""",
                (cutoff,),
            )
        except Exception as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise
    return int(cursor.rowcount or 0)


def run_startup_recovery(connect) -> dict:
    """Assess persisted invariants without making a broker network call."""
    set_runtime_state(connect, "recovering", reason="checking persisted order invariants")
    expired_legacy_count = close_expired_legacy_day_orders(connect)
    health = build_order_health(connect, include_runtime=False)
    state = "reduce_only" if health["blockers"] else "ready"
    reason = "startup blockers require reconciliation" if health["blockers"] else "persisted order invariants are healthy"
    return set_runtime_state(connect, state, reason=reason, details={
        "blockers": health["blockers"], "warnings": health["warnings"],
        "expired_legacy_day_orders": expired_legacy_count,
    })
