"""Print unified-order invariant violations without changing operational data."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.repository import connect_db, init_db


def inspect() -> dict:
    init_db()
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        invalid_fills = [dict(row) for row in conn.execute(
            "SELECT id,client_order_key,requested_qty,filled_qty,status FROM orders "
            "WHERE filled_qty < 0 OR filled_qty > requested_qty"
        ).fetchall()]
        duplicate_broker_orders = [dict(row) for row in conn.execute(
            """SELECT broker_order_id,COUNT(*) AS count FROM orders
               WHERE COALESCE(broker_order_id,'') <> ''
               GROUP BY broker_order_id HAVING COUNT(*) > 1"""
        ).fetchall()]
        fill_sum_mismatches = [dict(row) for row in conn.execute(
            """SELECT o.id,o.client_order_key,o.filled_qty,COALESCE(SUM(f.quantity),0) AS ledger_fill_qty
               FROM orders o LEFT JOIN fills f ON f.order_id=o.id
               GROUP BY o.id HAVING o.filled_qty <> COALESCE(SUM(f.quantity),0)"""
        ).fetchall()]
        stale_terminal = [dict(row) for row in conn.execute(
            """SELECT id,client_order_key,status,completed_at FROM orders
               WHERE status IN ('filled','canceled','rejected','expired') AND completed_at IS NULL"""
        ).fetchall()]
    violations = {
        "invalid_fill_quantities": invalid_fills,
        "duplicate_broker_order_ids": duplicate_broker_orders,
        "fill_sum_mismatches": fill_sum_mismatches,
        "terminal_orders_without_completed_at": stale_terminal,
    }
    return {
        "ok": not any(violations.values()),
        "violation_count": sum(len(rows) for rows in violations.values()),
        "violations": violations,
    }


if __name__ == "__main__":
    print(json.dumps(inspect(), ensure_ascii=False, indent=2))
