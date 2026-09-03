import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.application.orders.health import (
    NewRiskBlockedError,
    assert_new_risk_allowed,
    build_order_health,
)
from src.application.orders.approval import KST, default_domestic_expiry
from src.application.orders.models import OrderIntent
from src.application.orders.position_reconciliation import apply_latest_open_reconciliation_issues
from src.application.orders.repository import OrderLedgerRepository
from src.application.orders.recovery import (
    close_expired_legacy_day_orders,
    close_expired_unified_day_orders,
    reconcile_unknown_orders_from_legacy_fills,
    run_startup_recovery,
    sync_terminal_approval_orders,
)
from src.db.connection import open_sqlite
from src.db.migrations import apply_migrations


class UnifiedOrderLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "orders.db"

        def connect():
            return open_sqlite(self.db_path, row_factory=sqlite3.Row)

        self.connect = connect
        with self.connect() as conn:
            apply_migrations(conn)
        self.repository = OrderLedgerRepository(self.connect)

    def tearDown(self):
        self.temp_dir.cleanup()

    def intent(self, key="key-1"):
        return OrderIntent(
            client_order_key=key,
            correlation_id="correlation-1",
            symbol="005930",
            name="삼성전자",
            side="buy",
            quantity=10,
            price=70000,
            approval_id=1,
        )

    def test_create_is_idempotent(self):
        first = self.repository.create(self.intent())
        second = self.repository.create(self.intent())
        self.assertEqual(first["id"], second["id"])
        detail = self.repository.detail(first["id"])
        self.assertEqual(1, len(detail["events"]))

    def test_terminal_approval_sync_is_safe_before_legacy_table_exists(self):
        self.assertEqual(0, sync_terminal_approval_orders(self.connect))

    def test_after_close_approval_expires_at_next_market_session_close(self):
        from datetime import datetime

        current = datetime(2026, 8, 28, 18, 0, tzinfo=KST)
        self.assertEqual("2026-08-31 15:30:00", default_domestic_expiry(current))

    def test_transition_uses_expected_state(self):
        order = self.repository.create(self.intent())
        approved = self.repository.transition(order["id"], "approval_pending", "approved")
        self.assertEqual("approved", approved["status"])
        with self.assertRaises(RuntimeError):
            self.repository.transition(order["id"], "approval_pending", "approved")

    def test_reconciliation_materializes_only_fill_delta(self):
        order = self.repository.create(self.intent())
        self.repository.transition(order["id"], "approval_pending", "approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "submitted")
        self.repository.reconcile_snapshot(
            order["id"], status="open", cumulative_filled_qty=4,
            average_fill_price=69900, broker_order_id="123",
        )
        result = self.repository.reconcile_snapshot(
            order["id"], status="open", cumulative_filled_qty=10,
            average_fill_price=69950, broker_order_id="123",
        )
        self.assertEqual("filled", result["status"])
        self.assertEqual(10, result["filled_qty"])
        self.assertEqual([4, 6], [row["quantity"] for row in self.repository.detail(order["id"])["fills"]])
        with self.connect() as conn:
            position = conn.execute(
                "SELECT quantity,net_cash_flow FROM positions WHERE market='KR' AND symbol='005930'"
            ).fetchone()
        self.assertEqual(10, position["quantity"])
        self.assertAlmostEqual(-699500, position["net_cash_flow"])

    def test_startup_recovery_moves_to_ready_when_invariants_are_clean(self):
        recovery = run_startup_recovery(self.connect)
        self.assertEqual("ready", recovery["state"])
        assert_new_risk_allowed(self.connect)

    def test_health_surfaces_stale_and_expired_pending_approval_as_degraded(self):
        with self.connect() as conn:
            conn.execute(
                """CREATE TABLE approvals (
                       id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT,
                       symbol TEXT, name TEXT, action TEXT, qty INTEGER, price INTEGER,
                       status TEXT, expires_at TEXT
                   )"""
            )
            conn.execute(
                """INSERT INTO approvals (
                       created_at,updated_at,symbol,name,action,qty,price,status,expires_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    "2000-01-01 00:00:00", "2000-01-01 00:00:00", "005930",
                    "Samsung", "buy", 1, 70000, "pending", "2000-01-01 15:30:00",
                ),
            )
        health = build_order_health(self.connect, include_runtime=False)
        self.assertEqual("ready", health["state"])
        self.assertTrue(health["new_risk_allowed"])
        self.assertEqual("degraded", health["operational_status"])
        self.assertEqual(
            {"pending": 1, "stale_pending": 1, "expired_pending": 1},
            health["approvals"],
        )
        self.assertEqual(
            {"STALE_PENDING_APPROVAL", "EXPIRED_PENDING_APPROVAL"},
            {item["code"] for item in health["warnings"]},
        )

    def test_health_is_blocked_when_persisted_runtime_disagrees_with_clean_ledger(self):
        from src.application.orders.recovery import set_runtime_state

        set_runtime_state(self.connect, "reduce_only", reason="operator review")
        health = build_order_health(self.connect)
        self.assertEqual("reduce_only", health["state"])
        self.assertEqual("blocked", health["operational_status"])
        self.assertFalse(health["new_risk_allowed"])
        mismatch = next(item for item in health["warnings"] if item["code"] == "RUNTIME_STATE_MISMATCH")
        self.assertEqual("ready", mismatch["computed_state"])

    def test_stale_active_order_forces_reduce_only(self):
        order = self.repository.create(self.intent())
        self.repository.transition(order["id"], "approval_pending", "approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "submitted")
        with self.connect() as conn:
            conn.execute(
                "UPDATE orders SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (order["id"],),
            )
        recovery = run_startup_recovery(self.connect)
        self.assertEqual("reduce_only", recovery["state"])
        with self.assertRaises(NewRiskBlockedError):
            assert_new_risk_allowed(self.connect)

    def test_unknown_order_blocks_new_risk(self):
        order = self.repository.create(self.intent())
        self.repository.transition(order["id"], "approval_pending", "approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "broker_unknown")
        run_startup_recovery(self.connect)
        with self.assertRaises(NewRiskBlockedError):
            assert_new_risk_allowed(self.connect)

    def test_broker_identity_is_unique_per_account_and_market(self):
        first = self.repository.create(self.intent("first"), initial_status="approved")
        second = self.repository.create(self.intent("second"), initial_status="approved")
        self.repository.bind_broker_result(
            first["id"], "BROKER-1", broker_order_date="2026-08-28"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.bind_broker_result(
                second["id"], "BROKER-1", broker_order_date="2026-08-28"
            )

    def test_broker_identity_can_be_reused_on_a_later_trading_day(self):
        first = self.repository.create(self.intent("first"), initial_status="approved")
        second = self.repository.create(self.intent("second"), initial_status="approved")
        self.repository.bind_broker_result(
            first["id"], "BROKER-1", broker_order_date="2026-08-28"
        )
        self.repository.bind_broker_result(
            second["id"], "BROKER-1", broker_order_date="2026-08-31"
        )
        self.assertEqual("BROKER-1", self.repository.get(second["id"])["broker_order_id"])

    def test_broker_status_alias_is_normalized_and_invalid_status_is_rejected(self):
        order = self.repository.create(self.intent(), initial_status="approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "submitted")
        result = self.repository.reconcile_snapshot(
            order["id"], status="partially_filled", cumulative_filled_qty=2,
            average_fill_price=70000,
        )
        self.assertEqual("partial", result["status"])
        with self.assertRaises(ValueError):
            self.repository.reconcile_snapshot(
                order["id"], status="mystery", cumulative_filled_qty=2,
            )

    def test_startup_closes_only_prior_session_legacy_day_orders(self):
        with self.connect() as conn:
            conn.execute(
                """CREATE TABLE trades (
                       id INTEGER PRIMARY KEY, ts TEXT, order_status TEXT,
                       filled_qty REAL, response_msg TEXT
                   )"""
            )
            conn.execute(
                "INSERT INTO trades VALUES(1,'2026-08-28 10:00:00','partial',2,'imported')"
            )
            conn.execute(
                "INSERT INTO trades VALUES(2,'2026-08-31 09:00:00','open',0,'imported')"
            )
        closed = close_expired_legacy_day_orders(
            self.connect,
            now=datetime(2026, 8, 31, 9, 5, tzinfo=timezone(timedelta(hours=9))),
        )
        self.assertEqual(1, closed)
        with self.connect() as conn:
            rows = conn.execute("SELECT id,order_status,filled_qty FROM trades ORDER BY id").fetchall()
        self.assertEqual((1, "canceled", 2), tuple(rows[0]))
        self.assertEqual((2, "open", 0), tuple(rows[1]))

    def test_startup_closes_prior_session_unified_day_order_remainder(self):
        order = self.repository.create(self.intent(), initial_status="approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "submitted")
        with self.connect() as conn:
            conn.execute(
                "UPDATE orders SET broker_order_date='2026-08-28',status='partial',filled_qty=2 WHERE id=?",
                (order["id"],),
            )
        closed = close_expired_unified_day_orders(
            self.connect,
            now=datetime(2026, 8, 31, 9, 5, tzinfo=timezone(timedelta(hours=9))),
        )
        self.assertEqual(1, closed)
        result = self.repository.detail(order["id"])
        self.assertEqual("canceled", result["status"])
        self.assertEqual(2, result["filled_qty"])
        self.assertEqual("expired_day_order", result["events"][-1]["event_type"])

    def test_broker_balance_adjustment_is_audited_and_preserved_by_later_fills(self):
        order = self.repository.create(self.intent(), initial_status="approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "submitted")
        self.repository.reconcile_snapshot(
            order["id"], status="open", cumulative_filled_qty=4,
            average_fill_price=70000, broker_order_id="balance-test",
        )
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO reconciliation_adjustments
                   (account_key,market,symbol,broker_qty,internal_qty,difference_qty,
                    reason,status,snapshot_json,created_at)
                   VALUES('','KR','005930',7,4,3,'balance mismatch','open','{}','2026-08-31')"""
            )
        result = apply_latest_open_reconciliation_issues(self.connect, actor="test")
        self.assertEqual(1, result["applied_count"])
        with self.connect() as conn:
            issue = conn.execute(
                "SELECT status,reviewed_by FROM reconciliation_adjustments"
            ).fetchone()
            adjustment = conn.execute(
                "SELECT quantity_delta FROM position_quantity_adjustments"
            ).fetchone()
        self.assertEqual(("resolved", "test"), tuple(issue))
        self.assertEqual(3, adjustment[0])

        self.repository.reconcile_snapshot(
            order["id"], status="open", cumulative_filled_qty=6,
            average_fill_price=70000, broker_order_id="balance-test",
        )
        position = self.repository.list_positions(market="KR")[0]
        self.assertEqual(9, position["quantity"])

    def test_unknown_order_recovers_from_unique_verified_legacy_fill(self):
        order = self.repository.create(self.intent(), initial_status="approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "broker_unknown")
        with self.connect() as conn:
            conn.execute(
                """CREATE TABLE trades (
                    id INTEGER PRIMARY KEY,ts TEXT,symbol TEXT,action TEXT,qty INTEGER,
                    price REAL,filled_qty INTEGER,filled_price REAL,
                    order_status TEXT,broker_order_id TEXT
                )"""
            )
            conn.execute(
                "UPDATE orders SET created_at='2026-08-31T00:09:21+00:00' WHERE id=?",
                (order["id"],),
            )
            conn.execute(
                """INSERT INTO trades VALUES
                   (1,'2026-08-31 09:09:23','005930','buy',10,70000,10,70100,
                    'filled','0018447')"""
            )
        self.assertEqual(1, reconcile_unknown_orders_from_legacy_fills(self.connect))
        recovered = self.repository.get(order["id"])
        self.assertEqual("filled", recovered["status"])
        self.assertEqual("0018447", recovered["broker_order_id"])
        self.assertEqual(10, recovered["filled_qty"])

    def test_submitted_order_recovers_from_unique_verified_legacy_cancellation(self):
        order = self.repository.create(self.intent(), initial_status="approved")
        self.repository.transition(order["id"], "approved", "submitting")
        self.repository.transition(order["id"], "submitting", "submitted")
        with self.connect() as conn:
            conn.execute(
                """CREATE TABLE trades (
                    id INTEGER PRIMARY KEY,ts TEXT,symbol TEXT,action TEXT,qty INTEGER,
                    price REAL,filled_qty INTEGER,filled_price REAL,
                    order_status TEXT,broker_order_id TEXT
                )"""
            )
            conn.execute(
                "UPDATE orders SET created_at='2026-09-02T06:08:50+00:00' WHERE id=?",
                (order["id"],),
            )
            conn.execute(
                """INSERT INTO trades VALUES
                   (1,'2026-09-02 15:09:15','005930','buy',10,70000,0,0,
                    'canceled','0139396')"""
            )
        self.assertEqual(1, reconcile_unknown_orders_from_legacy_fills(self.connect))
        recovered = self.repository.get(order["id"])
        self.assertEqual("canceled", recovered["status"])
        self.assertEqual("0139396", recovered["broker_order_id"])
        self.assertEqual(0, recovered["filled_qty"])


if __name__ == "__main__":
    unittest.main()
