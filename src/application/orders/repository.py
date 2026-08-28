from __future__ import annotations

import json
from datetime import datetime, timezone

from src.application.orders.models import ALLOWED_TRANSITIONS, OrderIntent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OrderLedgerRepository:
    def __init__(self, connect):
        self._connect = connect

    def create(self, intent: OrderIntent, *, initial_status: str = "approval_pending") -> dict:
        now = _now()
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            conn.execute(
                """
                INSERT INTO orders (
                    client_order_key, correlation_id, account_key, market, symbol, name,
                    side, order_type, time_in_force, requested_qty, order_price, status,
                    strategy_id, strategy_version, signal_id, decision_id, approval_id,
                    broker_order_id, broker_order_date, expires_at, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_key) DO NOTHING
                """,
                (
                    intent.client_order_key, intent.correlation_id, intent.account_key,
                    intent.market, intent.symbol, intent.name, intent.side,
                    intent.order_type, intent.time_in_force, intent.quantity, intent.price,
                    initial_status, intent.strategy_id, intent.strategy_version,
                    intent.signal_id, intent.decision_id, intent.approval_id,
                    intent.broker_order_id, intent.broker_order_date or "",
                    intent.expires_at, json.dumps(intent.metadata, ensure_ascii=False), now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM orders WHERE client_order_key = ?", (intent.client_order_key,)
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to create or load order")
            order = dict(row)
            exists = conn.execute(
                "SELECT 1 FROM order_events WHERE order_id=? AND event_type='created'",
                (order["id"],),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO order_events(order_id,event_type,to_status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (order["id"], "created", initial_status, "{}", now),
                )
            return order

    def get(self, order_id: int) -> dict | None:
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            return dict(row) if row else None

    def get_by_approval(self, approval_id: int) -> dict | None:
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT * FROM orders WHERE approval_id=? ORDER BY id DESC LIMIT 1", (approval_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_by_broker_order_id(
        self, broker_order_id: str, *, broker_order_date: str = "",
        account_key: str = "", market: str = "KR",
    ) -> dict | None:
        if not str(broker_order_id or "").strip():
            return None
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                """SELECT * FROM orders WHERE broker_order_id=?
                   AND broker_order_date=? AND account_key=? AND market=?
                   ORDER BY id DESC LIMIT 1""",
                (str(broker_order_id), broker_order_date, account_key, market),
            ).fetchone()
            return dict(row) if row else None

    def transition(self, order_id: int, expected: str, target: str, *, actor="system", reason="", payload=None) -> dict:
        if target not in ALLOWED_TRANSITIONS.get(expected, set()):
            raise ValueError(f"invalid order transition: {expected} -> {target}")
        now = _now()
        completed = now if target in {"filled", "canceled", "rejected", "expired"} else None
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE orders SET status=?, version=version+1, updated_at=?,
                   submitted_at=CASE WHEN ?='submitted' THEN COALESCE(submitted_at,?) ELSE submitted_at END,
                   completed_at=COALESCE(?, completed_at)
                   WHERE id=? AND status=?""",
                (target, now, target, now, completed, order_id, expected),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("order state changed concurrently")
            conn.execute(
                """INSERT INTO order_events(order_id,event_type,from_status,to_status,actor,reason,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (order_id, target, expected, target, actor, reason, json.dumps(payload or {}, ensure_ascii=False), now),
            )
        result = self.get(order_id)
        if result is None:
            raise RuntimeError("order disappeared after transition")
        return result

    def bind_broker_result(
        self, order_id: int, broker_order_id: str, *, broker_order_date: str = "", message=""
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE orders SET broker_order_id=?,
                   broker_order_date=COALESCE(NULLIF(?,''),broker_order_date),
                   last_error_message=?, updated_at=? WHERE id=?""",
                (broker_order_id or None, broker_order_date, message or None, _now(), order_id),
            )

    def reconcile_snapshot(
        self,
        order_id: int,
        *,
        status: str,
        cumulative_filled_qty: int,
        average_fill_price: float = 0,
        broker_order_id: str = "",
        broker_order_date: str = "",
        raw=None,
    ) -> dict:
        """Apply a monotonic broker snapshot and materialize only its fill delta."""
        aliases = {
            "accepted": "submitted", "pending": "open", "working": "open",
            "partially_filled": "partial", "cancelled": "canceled",
            "failed": "rejected", "unknown": "broker_unknown",
        }
        status = aliases.get(str(status).lower(), str(status).lower())
        valid_statuses = set(ALLOWED_TRANSITIONS) | {
            target for targets in ALLOWED_TRANSITIONS.values() for target in targets
        }
        if status not in valid_statuses:
            raise ValueError(f"unsupported broker order status: {status}")
        now = _now()
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise KeyError(f"order not found: {order_id}")
            current = dict(row)
            requested = float(current["requested_qty"])
            old_filled = float(current["filled_qty"])
            incoming = float(cumulative_filled_qty)
            if incoming < old_filled or incoming > requested:
                raise ValueError(
                    f"invalid cumulative fill quantity: {old_filled} -> {incoming}/{requested}"
                )
            previous_status = str(current["status"])
            if incoming == requested and requested > 0:
                status = "filled"
            elif incoming > 0 and status not in {"canceled", "filled"}:
                status = "partial"
            if previous_status in {"filled", "canceled", "rejected", "expired"}:
                if incoming == old_filled and status != previous_status:
                    raise ValueError(
                        f"broker snapshot cannot regress terminal order: {previous_status} -> {status}"
                    )
            if previous_status != status and status not in ALLOWED_TRANSITIONS.get(previous_status, set()):
                raise ValueError(f"invalid broker order transition: {previous_status} -> {status}")
            delta = incoming - old_filled
            if delta:
                identity = broker_order_id or str(current.get("broker_order_id") or order_id)
                identity_date = broker_order_date or str(current.get("broker_order_date") or "")
                fill_key = (
                    f"snapshot:{current['account_key']}:{current['market']}:"
                    f"{identity_date}:{identity}:{incoming}"
                )
                cumulative_value = incoming * float(average_fill_price or 0)
                previous_value = old_filled * float(current.get("average_fill_price") or 0)
                delta_price = max(0.0, (cumulative_value - previous_value) / delta)
                conn.execute(
                    """INSERT INTO fills
                       (fill_key,order_id,quantity,price,cost_source,filled_at,raw_json)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(fill_key) DO NOTHING""",
                    (
                        fill_key, order_id, delta, delta_price,
                        "unavailable", now, json.dumps(raw or {}, ensure_ascii=False),
                    ),
                )
                signed_quantity = "CASE WHEN o.side='buy' THEN f.quantity ELSE -f.quantity END"
                signed_cash = "CASE WHEN o.side='buy' THEN -(f.quantity*f.price) ELSE (f.quantity*f.price) END"
                projection = conn.execute(
                    f"""SELECT COALESCE(SUM({signed_quantity}),0), COALESCE(SUM({signed_cash}),0)
                        FROM fills f JOIN orders o ON o.id=f.order_id
                        WHERE o.account_key=? AND o.market=? AND o.symbol=?""",
                    (current["account_key"], current["market"], current["symbol"]),
                ).fetchone()
                conn.execute(
                    """INSERT INTO positions(account_key,market,symbol,quantity,net_cash_flow,updated_at)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(account_key,market,symbol) DO UPDATE SET
                       quantity=excluded.quantity, net_cash_flow=excluded.net_cash_flow,
                       updated_at=excluded.updated_at""",
                    (current["account_key"], current["market"], current["symbol"],
                     projection[0], float(projection[1]), now),
                )
            completed = now if status in {"filled", "canceled", "rejected", "expired"} else None
            conn.execute(
                """UPDATE orders SET status=?, filled_qty=?, average_fill_price=?,
                   broker_order_id=COALESCE(NULLIF(?,''),broker_order_id), last_synced_at=?,
                   broker_order_date=COALESCE(NULLIF(?,''),broker_order_date),
                   completed_at=COALESCE(?,completed_at), version=version+1, updated_at=?
                   WHERE id=?""",
                (status, incoming, average_fill_price, broker_order_id, now,
                 broker_order_date, completed, now, order_id),
            )
            if previous_status != status or delta:
                conn.execute(
                    """INSERT INTO order_events
                       (order_id,event_type,from_status,to_status,actor,reason,payload_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        order_id, "broker_snapshot", previous_status, status, "reconciliation",
                        "broker order history synchronized",
                        json.dumps({"fill_delta": delta, "cumulative_filled_qty": incoming}, ensure_ascii=False),
                        now,
                    ),
                )
        result = self.get(order_id)
        if result is None:
            raise RuntimeError("order disappeared after reconciliation")
        return result

    def list_orders(self, *, statuses=(), limit=100, offset=0) -> list[dict]:
        limit = min(500, max(1, int(limit)))
        offset = max(0, int(offset))
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT ? OFFSET ?",
                    (*statuses, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
            return [dict(row) for row in rows]

    def detail(self, order_id: int) -> dict | None:
        order = self.get(order_id)
        if not order:
            return None
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            order["events"] = [dict(row) for row in conn.execute(
                "SELECT * FROM order_events WHERE order_id=? ORDER BY id", (order_id,)
            ).fetchall()]
            order["fills"] = [dict(row) for row in conn.execute(
                "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at,id", (order_id,)
            ).fetchall()]
        return order

    def list_positions(self, *, market: str | None = None) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            if market:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE market=? ORDER BY symbol", (market,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM positions ORDER BY market,symbol"
                ).fetchall()
            return [dict(row) for row in rows]
