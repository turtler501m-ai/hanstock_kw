"""Compatibility bridge while legacy approval/trade paths are being retired."""

from __future__ import annotations

import hashlib
import uuid

from src.application.orders.models import OrderIntent
from src.application.orders.repository import OrderLedgerRepository
from src.application.orders.identity import broker_account_scope_key


def _key(approval: dict) -> str:
    existing = str(approval.get("client_order_key") or "").strip()
    if existing:
        return existing
    raw = "|".join(
        str(approval.get(name) or "")
        for name in ("id", "created_at", "symbol", "action", "qty", "price")
    )
    return "legacy-approval-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def ensure_approval_order(connect, approval: dict) -> dict | None:
    repository = OrderLedgerRepository(connect)
    approval_id = int(approval["id"])
    existing = repository.get_by_approval(approval_id)
    if existing:
        return existing
    intent = OrderIntent(
        client_order_key=_key(approval),
        correlation_id=str(approval.get("correlation_id") or uuid.uuid4()),
        account_key=str(approval.get("account_key") or ""),
        symbol=str(approval.get("symbol") or ""),
        name=str(approval.get("name") or ""),
        side=str(approval.get("action") or "").lower(),
        quantity=int(approval.get("qty") or 0),
        price=float(approval.get("price") or 0),
        market=str(approval.get("market") or "KR").upper(),
        order_type="market" if float(approval.get("price") or 0) == 0 else "limit",
        strategy_id=approval.get("strategy_id"),
        strategy_version=approval.get("strategy_version"),
        decision_id=approval.get("decision_id"),
        approval_id=approval_id,
        expires_at=approval.get("expires_at"),
        metadata={"legacy_source": approval.get("source"), "reason": approval.get("reason")},
    )
    try:
        return repository.create(intent, initial_status="approval_pending")
    except (KeyError, TypeError):
        # Some isolated callers inject a non-database test double. The bridge
        # is shadow infrastructure during migration and must not alter the
        # established broker path when no real persistence contract exists.
        return None


def mirror_status(connect, order: dict | None, target: str, *, actor: str, reason: str = "") -> dict | None:
    if not order:
        return None
    repository = OrderLedgerRepository(connect)
    current = repository.get(int(order["id"])) or order
    status = str(current["status"])
    paths = {
        "submitting": ["approved", "submitting"],
        "submitted": ["approved", "submitting", "submitted"],
        "broker_unknown": ["approved", "submitting", "broker_unknown"],
        "rejected": ["rejected"],
        "failed": ["approved", "submitting", "failed"],
        "expired": ["expired"],
    }
    path = paths.get(target, [target])
    if status in path:
        path = path[path.index(status) + 1:]
    for next_status in path:
        if status == next_status:
            continue
        current = repository.transition(
            int(order["id"]), status, next_status, actor=actor, reason=reason
        )
        status = next_status
    return current


def backfill_active_legacy_orders(connect) -> dict:
    """Mirror unresolved legacy domestic trades before recovery health checks."""
    with connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE order_status IN ('submitted','open','partial','broker_unknown')
               ORDER BY id"""
        ).fetchall()
    repository = OrderLedgerRepository(connect)
    account_key = broker_account_scope_key("KR")
    imported = 0
    skipped = 0
    for raw in rows:
        trade = dict(raw)
        trade_id = int(trade["id"])
        order_date = str(trade.get("ts") or "")[:10]
        broker_order_id = str(trade.get("broker_order_id") or "").strip()
        existing = repository.get_by_broker_order_id(
            broker_order_id, broker_order_date=order_date,
            account_key=account_key, market="KR",
        ) if broker_order_id else None
        if existing is not None:
            skipped += 1
            continue
        status = str(trade.get("order_status") or "broker_unknown")
        intent = OrderIntent(
            client_order_key=f"legacy-trade:{trade_id}",
            correlation_id=f"legacy-trade:{trade_id}",
            account_key=account_key,
            market="KR",
            symbol=str(trade.get("symbol") or ""),
            name=str(trade.get("name") or trade.get("symbol") or ""),
            side=str(trade.get("action") or "").lower(),
            quantity=int(trade.get("qty") or 0),
            price=float(trade.get("price") or 0),
            strategy_id=trade.get("strategy_id"),
            approval_id=int(trade.get("source_approval_id") or 0) or None,
            broker_order_id=broker_order_id or None,
            broker_order_date=order_date,
            metadata={"legacy_trade_id": trade_id, "backfilled": True},
        )
        order = repository.create(intent, initial_status=status)
        filled_qty = int(trade.get("filled_qty") or 0)
        if filled_qty > 0:
            repository.reconcile_snapshot(
                int(order["id"]), status=status,
                cumulative_filled_qty=filled_qty,
                average_fill_price=float(trade.get("filled_price") or 0),
                broker_order_id=broker_order_id,
                broker_order_date=order_date,
                raw={"legacy_trade_id": trade_id},
            )
        imported += 1
    return {"checked_count": len(rows), "imported_count": imported, "skipped_count": skipped}
