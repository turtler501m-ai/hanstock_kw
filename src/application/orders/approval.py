"""Canonical domestic approval creation shared by strategies and dashboards."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from src.application.orders.legacy_bridge import ensure_approval_order
from src.utils.market_calendar import is_market_session

KST = timezone(timedelta(hours=9))


def default_domestic_expiry(current: datetime) -> str:
    """Return the close of the current or next KRX trading session."""
    candidate = current
    for _ in range(10):
        close = candidate.replace(hour=15, minute=30, second=0, microsecond=0)
        if is_market_session("KR", candidate) and current < close:
            return close.strftime("%Y-%m-%d %H:%M:%S")
        candidate = (candidate + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    raise RuntimeError("could not resolve the next KRX approval expiry")


def create_domestic_approval(
    *,
    connect: Callable,
    init_db: Callable[[], None],
    symbol: str,
    name: str,
    action: str,
    qty: int,
    price: int,
    reason: str = "",
    source: str = "application",
    strategy_id: str | None = None,
    strategy_version: int | None = None,
    profile_hash: str | None = None,
    source_candidate_id: int | None = None,
    decision_id: int | None = None,
    managed_order_id: int | None = None,
    client_order_key: str | None = None,
    correlation_id: str | None = None,
    expires_at: str | None = None,
    now: datetime | None = None,
) -> int:
    action = str(action).lower()
    if action not in {"buy", "sell"}:
        raise ValueError("action must be buy or sell")
    if not str(symbol).strip() or int(qty) <= 0:
        raise ValueError("symbol and positive qty are required")
    init_db()
    current = now or datetime.now(KST)
    created_at = current.strftime("%Y-%m-%d %H:%M:%S")
    expires_at = expires_at or default_domestic_expiry(current)
    correlation_id = correlation_id or str(uuid.uuid4())
    client_order_key = str(client_order_key or "").strip() or None
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO approvals (
                created_at,updated_at,symbol,name,action,qty,price,reason,source,
                status,response_msg,strategy_id,strategy_version,profile_hash,
                source_candidate_id,decision_id,managed_order_id,client_order_key,
                expires_at,correlation_id
            ) VALUES (?,?,?,?,?,?,?,?,?,'pending','',?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
            """,
            (
                created_at, created_at, symbol, name or symbol, action, int(qty), int(price),
                reason, source, strategy_id, strategy_version, profile_hash,
                source_candidate_id, decision_id, managed_order_id, client_order_key,
                expires_at, correlation_id,
            ),
        )
        if cursor.rowcount == 0 and client_order_key:
            row = conn.execute(
                "SELECT id FROM approvals WHERE client_order_key=?", (client_order_key,)
            ).fetchone()
            if row is None:
                raise RuntimeError("approval conflict did not resolve to idempotency key")
            approval_id = int(row[0])
        else:
            approval_id = int(cursor.lastrowid)
    ensure_approval_order(connect, {
        "id": approval_id, "created_at": created_at, "symbol": symbol,
        "name": name or symbol, "action": action, "qty": int(qty),
        "price": int(price), "reason": reason, "source": source,
        "strategy_id": strategy_id, "strategy_version": strategy_version,
        "decision_id": decision_id, "expires_at": expires_at,
        "correlation_id": correlation_id, "client_order_key": client_order_key,
        "managed_order_id": managed_order_id,
    })
    return approval_id
