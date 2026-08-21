import unittest
import tempfile
from unittest.mock import patch

from src import trader
from src.dashboard.routes.account import _attach_holding_strategies, _summarize_holding_strategies
from src.dashboard.routes.stock_order import (
    _allocate_strategy_reconciliation,
    _balance_sync_strategy_id,
)


class HoldingStrategySummaryTests(unittest.TestCase):
    def test_balance_sync_without_owner_uses_broker_baseline(self):
        self.assertEqual(
            _allocate_strategy_reconciliation(7, {}, action="buy"),
            [("broker_account_baseline", 7)],
        )
        self.assertEqual(
            _balance_sync_strategy_id({"reason": "증권사 잔고 전략귀속 동기화"}),
            "broker_account_baseline",
        )
        self.assertEqual(_balance_sync_strategy_id({"reason": "manual buy"}), "")

    def test_legacy_balance_sync_is_attached_as_broker_baseline(self):
        original_path = trader.config.trade_db_path
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                trader.config.trade_db_path = f"{tmpdir}/trades.sqlite"
                trader.init_db()
                trader.save_trade(
                    "005930", "Samsung", "buy", 2, 80000,
                    "증권사 잔고 전략귀속 동기화", True, False,
                    order_status="reconciled", filled_qty=2, filled_price=80000,
                )
                parsed = {"holdings": [{
                    "symbol": "005930", "qty": 2, "value": 160000, "pnl": 0,
                }]}
                with patch("src.db.repository.load_ai_strategies", return_value=[]):
                    result = _attach_holding_strategies(parsed)

                self.assertEqual(result["holdings"][0]["strategy_ids"], ["broker_account_baseline"])
                self.assertEqual(result["holding_summary"]["attribution_coverage"], 100.0)
        finally:
            trader.config.trade_db_path = original_path

    def test_strategy_quantities_are_scaled_to_broker_quantity(self):
        parsed = {
            "holdings": [{
                "symbol": "005930",
                "qty": 10,
                "value": 1_000_000,
                "pnl": -100_000,
                "strategies": [
                    {"id": "strategy_a", "name": "전략 A", "qty": 6},
                    {"id": "strategy_b", "name": "전략 B", "qty": 6},
                ],
            }]
        }

        result = _summarize_holding_strategies(parsed)

        allocations = result["holdings"][0]["strategy_allocations"]
        self.assertEqual([item["allocated_qty"] for item in allocations], [5.0, 5.0])
        self.assertEqual(sum(item["evaluation_amount"] for item in allocations), 1_000_000)
        self.assertEqual(sum(item["pnl"] for item in allocations), -100_000)
        self.assertEqual(result["holding_summary"]["attribution_coverage"], 100.0)
        self.assertTrue(result["strategy_summary"][0]["is_loss"])

    def test_unattributed_quantity_is_reported_separately(self):
        parsed = {
            "holdings": [{
                "symbol": "000660",
                "qty": 10,
                "value": 2_000_000,
                "pnl": 200_000,
                "strategies": [
                    {"id": "strategy_a", "name": "전략 A", "qty": 6},
                ],
            }]
        }

        result = _summarize_holding_strategies(parsed)

        summaries = {
            item["strategy_id"]: item
            for item in result["strategy_summary"]
        }
        self.assertEqual(summaries["strategy_a"]["evaluation_amount"], 1_200_000)
        self.assertEqual(summaries["unattributed"]["evaluation_amount"], 800_000)
        self.assertEqual(result["holding_summary"]["attribution_coverage"], 60.0)

    def test_scaled_allocations_use_whole_shares_without_unattributed_row(self):
        parsed = {
            "holdings": [{
                "symbol": "196170",
                "qty": 29,
                "value": 10_005_000,
                "pnl": -101_500,
                "strategies": [
                    {"id": "heikin_ashi_scalping_strategy", "name": "하이킨아시", "qty": 53},
                    {"id": "ai_rebalance", "name": "AI 리밸런싱", "qty": 8},
                ],
            }]
        }

        result = _summarize_holding_strategies(parsed)

        allocations = result["holdings"][0]["strategy_allocations"]
        self.assertEqual(
            [item["strategy_id"] for item in allocations],
            ["heikin_ashi_scalping_strategy", "ai_rebalance"],
        )
        self.assertEqual([item["allocated_qty"] for item in allocations], [25.0, 4.0])
        self.assertEqual(sum(item["allocated_qty"] for item in allocations), 29.0)
        self.assertEqual(result["holding_summary"]["attribution_coverage"], 100.0)

    def test_holding_summary_counts_profit_loss_and_flat_positions(self):
        parsed = {
            "holdings": [
                {"symbol": "A", "qty": 1, "value": 100, "pnl": 10, "strategies": []},
                {"symbol": "B", "qty": 1, "value": 100, "pnl": -5, "strategies": []},
                {"symbol": "C", "qty": 1, "value": 100, "pnl": 0, "strategies": []},
            ]
        }

        result = _summarize_holding_strategies(parsed)

        self.assertEqual(result["holding_summary"]["total_count"], 3)
        self.assertEqual(result["holding_summary"]["profit_count"], 1)
        self.assertEqual(result["holding_summary"]["loss_count"], 1)
        self.assertEqual(result["holding_summary"]["flat_count"], 1)


if __name__ == "__main__":
    unittest.main()
