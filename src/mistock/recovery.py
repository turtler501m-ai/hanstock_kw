"""Recover the US ledger using the Mistock schema and verified rejections."""

import re

from src.application.orders.health import build_order_health
from src.application.orders.recovery import close_expired_unified_day_orders, set_runtime_state
from src.application.orders.repository import OrderLedgerRepository


def is_symbol_rejection(message: str) -> bool:
    # Broker error 1903 explicitly rejects an unknown symbol/exchange pair.
    return bool(re.search(r"\[1903:종목 정보가 없습니다\.", message))


def run_mistock_recovery(connect) -> dict:
    """Preserve uncertain outcomes; never infer a fill from expired DAY orders."""
    expired = close_expired_unified_day_orders(connect)
    repository = OrderLedgerRepository(connect)
    with connect() as conn:
        rows = conn.execute(
            """SELECT o.id, o.symbol, m.last_error
               FROM orders o JOIN managed_orders m
                 ON o.client_order_key=m.client_order_key
               WHERE o.market='US' AND o.status='broker_unknown'
                 AND COALESCE(o.broker_order_id,'')=''
                 AND COALESCE(m.broker_order_no,'')=''
                 AND o.filled_qty=0 AND m.filled_qty=0
                 AND o.symbol=m.symbol AND o.side=m.action
                 AND o.requested_qty=m.requested_qty AND m.status='failed'
                 AND EXISTS (
                   SELECT 1 FROM order_events e WHERE e.order_id=o.id
                     AND e.to_status='broker_unknown' AND e.reason=m.last_error
                 )"""
        ).fetchall()
    rejected = 0
    for order_id, symbol, error in rows:
        message = str(error or '')
        if (message.startswith(('Kiwoom ust20000 failed:', 'Kiwoom ust20001 failed:'))
                and is_symbol_rejection(message)
                and f'종목코드={symbol}]' in message):
            repository.transition(
                order_id, 'broker_unknown', 'rejected', actor='mistock_recovery',
                reason='Verified broker symbol rejection: ' + message,
            )
            rejected += 1
    with connect() as conn:
        conn.execute(
            """UPDATE managed_orders SET status='expired',
                   last_error=COALESCE(last_error,'DAY order expired without verified fill')
               WHERE status IN ('accepted','partial','partially_filled','cancel_requested')
                 AND client_order_key IN (
                   SELECT client_order_key FROM orders WHERE market='US' AND status='canceled'
                 )"""
        )
    health = build_order_health(connect, include_runtime=False)
    return set_runtime_state(
        connect, 'reduce_only' if health['blockers'] else 'ready',
        reason='Mistock persisted order invariants checked',
        details={'blockers': health['blockers'], 'expired_day_orders': expired,
                 'verified_rejections': rejected},
    )
