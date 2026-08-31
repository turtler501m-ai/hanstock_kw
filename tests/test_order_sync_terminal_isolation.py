import unittest
import sqlite3
import tempfile
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.dashboard.services import order_sync_service


class OrderSyncTerminalIsolationTests(unittest.TestCase):
    def test_filled_history_import_preserves_canceled_legacy_order(self):
        from src.dashboard import core as dashboard

        original_db_path = dashboard.trader.config.trade_db_path
        original_fetch_cloud_trades = dashboard.fetch_cloud_trades

        class _FakeAPI:
            def get_trade_history(self, _start_date, _end_date):
                return [{
                    "odno": "C12345",
                    "pdno": "005930",
                    "prdt_name": "Samsung",
                    "sll_buy_dvsn_cd": "01",
                    "ord_dt": "20260831",
                    "ord_tmd": "100000",
                    "ord_qty": "10",
                    "tot_ccld_qty": "4",
                    "rmn_qty": "6",
                    "avg_prvs": "70100",
                }]

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                dashboard.trader.config.trade_db_path = f"{tmpdir}/trades.sqlite"
                dashboard.fetch_cloud_trades = lambda: []
                dashboard.trader.save_trade(
                    "005930", "Samsung", "sell", 10, 0, "canceled sell",
                    True, True, broker_order_id="C12345", order_status="canceled",
                    filled_qty=0,
                )

                result = dashboard._sync_filled_trades_from_history(_FakeAPI(), days=1)

                with closing(sqlite3.connect(dashboard.trader.config.trade_db_path)) as conn:
                    row = conn.execute(
                        "SELECT order_status,filled_qty FROM trades WHERE broker_order_id='C12345'"
                    ).fetchone()
                self.assertEqual(row, ("canceled", 0))
                self.assertEqual(result["terminal_regression_count"], 1)
                self.assertEqual(
                    result["items"][0]["sync_result"],
                    "ignored_terminal_regression",
                )
        finally:
            dashboard.trader.config.trade_db_path = original_db_path
            dashboard.fetch_cloud_trades = original_fetch_cloud_trades

    def test_terminal_regression_does_not_block_following_order(self):
        order_sync_service._refresh_dependencies()
        tracked = [
            {"id": 1, "broker_order_id": "OLD", "symbol": "000001", "name": "old",
             "action": "sell", "qty": 1, "order_status": "canceled", "filled_qty": 0},
            {"id": 2, "broker_order_id": "NEW", "symbol": "000002", "name": "new",
             "action": "sell", "qty": 1, "order_status": "submitted", "filled_qty": 0},
        ]
        history = [{"id": "OLD", "remaining": 1, "filled": 0},
                   {"id": "NEW", "remaining": 0, "filled": 1}]
        update_status = Mock(return_value=1)
        trader = SimpleNamespace(
            update_trade_order_status=update_status,
            datetime=SimpleNamespace(now=lambda _tz: SimpleNamespace(strftime=lambda _fmt: "2026-08-31")),
            KST=object(),
        )

        def mirror(snapshot, _stored):
            if snapshot["broker_order_id"] == "OLD":
                raise ValueError("broker snapshot cannot regress terminal order: canceled -> open")

        replacements = {
            "_refresh_dependencies": Mock(),
            "_load_trackable_order_trades": Mock(return_value=tracked),
            "_order_history_window": Mock(return_value=("20260801", "20260831")),
            "_history_matches_tracked_order": lambda row, trade: row["id"] == trade["broker_order_id"],
            "_history_fill_qty": lambda row: row["filled"],
            "_history_fill_price": lambda _row: 100,
            "_history_remaining_qty": lambda row: row["remaining"],
            "_history_timestamp": lambda _row: "2026-08-31 10:00:00",
            "_history_order_is_canceled": lambda _row: False,
            "_history_order_is_rejected": lambda _row: False,
            "_mirror_trade_to_unified_ledger": mirror,
            "_to_int": lambda value: int(value or 0),
            "trader": trader,
        }
        with patch.multiple(order_sync_service, **replacements):
            result = order_sync_service._sync_order_status_from_history(
                object(), days=1, history=history
            )

        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["terminal_regression_count"], 1)
        self.assertEqual(result["orders"][0]["sync_result"], "ignored_terminal_regression")
        self.assertEqual(result["orders"][1]["order_status"], "filled")
        update_status.assert_called_once()

    def test_sync_outcome_is_partial_when_history_failed(self):
        from src.dashboard.routes import stock_order

        result = stock_order._classify_trade_sync_outcome(
            sync_items=[],
            history_sync=None,
            order_status_sync=None,
            history_error="history 500",
            order_status_error="history 500",
        )

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["ok"])

    def test_sync_outcome_requires_review_for_balance_mismatch(self):
        from src.dashboard.routes import stock_order

        result = stock_order._classify_trade_sync_outcome(
            sync_items=[{"sync_result": "review_required"}],
            history_sync={"ok": True},
            order_status_sync={"ok": True},
            history_error=None,
            order_status_error=None,
        )

        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["ok"])
        self.assertEqual(result["review_required_count"], 1)

    def test_sync_outcome_is_partial_for_isolated_terminal_snapshot(self):
        from src.dashboard.routes import stock_order

        result = stock_order._classify_trade_sync_outcome(
            sync_items=[{"sync_result": "ignored_terminal_regression"}],
            history_sync={"ok": True, "terminal_regression_count": 1},
            order_status_sync={"ok": True, "terminal_regression_count": 1},
            history_error=None,
            order_status_error=None,
        )

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["ok"])
        self.assertEqual(result["terminal_regression_count"], 2)


if __name__ == "__main__":
    unittest.main()
