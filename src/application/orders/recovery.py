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


def close_expired_unified_day_orders(connect, *, now: datetime | None = None) -> int:
    """Close non-ambiguous unified DAY orders from completed sessions."""
    kst = timezone(timedelta(hours=9))
    current = now or datetime.now(kst)
    cutoff = current.astimezone(kst).strftime("%Y-%m-%d")
    updated_at = current.astimezone(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute(
            """SELECT id,status FROM orders
               WHERE time_in_force='DAY'
                 AND broker_order_date<>'' AND broker_order_date<?
                 AND status IN ('submitted','open','partial','cancel_pending')""",
            (cutoff,),
        ).fetchall()
        for order_id, previous_status in rows:
            conn.execute(
                """UPDATE orders SET status='canceled',completed_at=COALESCE(completed_at,?),
                   updated_at=?,version=version+1 WHERE id=? AND status=?""",
                (updated_at, updated_at, order_id, previous_status),
            )
            conn.execute(
                """INSERT INTO order_events
                   (order_id,event_type,from_status,to_status,actor,reason,payload_json,created_at)
                   VALUES(?,'expired_day_order',?,'canceled','startup_recovery',
                          'prior-session DAY order remainder expired','{}',?)""",
                (order_id, previous_status, updated_at),
            )
    return len(rows)


def reconcile_unknown_orders_from_legacy_fills(connect) -> int:
    """Recover response-lost unified orders from a unique verified legacy fill.

    The legacy history synchronizer can learn the broker order number after the
    strategy router has already persisted an outcome-unknown unified order.
    Exact side/symbol/quantity and a five-minute timestamp window make the link
    conservative; ambiguous matches remain blocked for operator review.
    """
    from src.application.orders.repository import OrderLedgerRepository

    with connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        try:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
            ).fetchone():
                return 0
            unknown = [dict(row) for row in conn.execute(
                """SELECT * FROM orders WHERE status='broker_unknown'
                   AND COALESCE(broker_order_id,'')='' ORDER BY id"""
            ).fetchall()]
        except Exception as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise
    repository = OrderLedgerRepository(connect)
    recovered = 0
    for order in unknown:
        with connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            matches = conn.execute(
                """SELECT * FROM trades t
                   WHERE t.symbol=? AND lower(t.action)=? AND CAST(t.qty AS INTEGER)=?
                     AND t.order_status IN ('filled','reconciled')
                     AND CAST(COALESCE(t.filled_qty,0) AS INTEGER)=?
                     AND COALESCE(t.broker_order_id,'')<>''
                     AND abs((julianday(replace(t.ts,'T',' ')) -
                              (julianday(?) + 0.375)) * 86400) <= 300
                     AND NOT EXISTS (
                       SELECT 1 FROM orders linked
                       WHERE linked.id<>? AND linked.broker_order_date=substr(t.ts,1,10)
                         AND linked.broker_order_id=t.broker_order_id
                     )
                   ORDER BY t.id""",
                (
                    order["symbol"], str(order["side"]).lower(),
                    int(order["requested_qty"]), int(order["requested_qty"]),
                    order["created_at"], int(order["id"]),
                ),
            ).fetchall()
        if len(matches) != 1:
            continue
        trade = dict(matches[0])
        broker_order_id = str(trade["broker_order_id"])
        broker_order_date = str(trade["ts"])[:10]
        repository.bind_broker_result(
            int(order["id"]), broker_order_id,
            broker_order_date=broker_order_date,
            message="Recovered from verified legacy broker history",
        )
        repository.reconcile_snapshot(
            int(order["id"]), status="filled",
            cumulative_filled_qty=int(trade["filled_qty"]),
            average_fill_price=float(trade["filled_price"] or trade["price"] or 0),
            broker_order_id=broker_order_id, broker_order_date=broker_order_date,
            raw={"legacy_trade_id": int(trade["id"]), "startup_recovery": True},
        )
        recovered += 1
    return recovered


def run_startup_recovery(connect) -> dict:
    """Assess persisted invariants without making a broker network call."""
    set_runtime_state(connect, "recovering", reason="checking persisted order invariants")
    expired_legacy_count = close_expired_legacy_day_orders(connect)
    expired_unified_count = close_expired_unified_day_orders(connect)
    recovered_unknown_count = reconcile_unknown_orders_from_legacy_fills(connect)
    health = build_order_health(connect, include_runtime=False)
    state = "reduce_only" if health["blockers"] else "ready"
    reason = "startup blockers require reconciliation" if health["blockers"] else "persisted order invariants are healthy"
    return set_runtime_state(connect, state, reason=reason, details={
        "blockers": health["blockers"], "warnings": health["warnings"],
        "expired_legacy_day_orders": expired_legacy_count,
        "expired_unified_day_orders": expired_unified_count,
        "recovered_unknown_orders": recovered_unknown_count,
    })
