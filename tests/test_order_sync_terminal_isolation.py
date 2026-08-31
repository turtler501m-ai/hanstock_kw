import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.dashboard.services import order_sync_service


class OrderSyncTerminalIsolationTests(unittest.TestCase):
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
        self.assertEqual(result["orders"][0]["sync_result"], "ignored_terminal_regression")
        self.assertEqual(result["orders"][1]["order_status"], "filled")
        update_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
