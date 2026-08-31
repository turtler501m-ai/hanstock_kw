import tempfile
import unittest
from unittest.mock import ANY, patch

from src.dashboard.routes import stock_order
import src.dashboard as dashboard


class StrategyAttributionSellTests(unittest.TestCase):
    def test_single_symbol_uses_server_allocation_and_sellable_quantity(self):
        parsed = {
            "holdings": [{
                "symbol": "196170",
                "name": "알테오젠",
                "qty": 29,
                "sellable_qty": 3,
            }]
        }

        def attach(data):
            data["holdings"][0]["strategy_allocations"] = [
                {"strategy_id": "ai_rebalance", "strategy_name": "AI 리밸런싱", "allocated_qty": 4}
            ]
            return data

        with patch.object(stock_order, "_get_api", return_value=object()), patch.object(
            stock_order, "_get_balance_data", return_value={}
        ), patch.object(stock_order, "_parse_balance", return_value=parsed), patch(
            "src.dashboard.routes.account._attach_holding_strategies", side_effect=attach
        ), patch.object(stock_order, "_unsubmitted_dashboard_sell_symbols", return_value=set()):
            orders, skipped = stock_order._strategy_attribution_sell_orders(
                "ai_rebalance", symbol="196170"
            )

        self.assertEqual(skipped, [])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 3)
        self.assertEqual(orders[0]["strategy_id"], "ai_rebalance")
        self.assertEqual(orders[0]["source"], "dashboard_strategy_holding_sell")

    def test_strategy_sell_all_queues_each_attributed_holding(self):
        original_db_path = stock_order.trader.config.trade_db_path
        orders = [
            {
                "symbol": "196170", "name": "알테오젠", "action": "sell", "qty": 4,
                "price": 0, "reason": "strategy sell", "source": "dashboard_strategy_sell_all",
                "strategy_id": "ai_rebalance",
            },
            {
                "symbol": "005930", "name": "삼성전자", "action": "sell", "qty": 2,
                "price": 0, "reason": "strategy sell", "source": "dashboard_strategy_sell_all",
                "strategy_id": "ai_rebalance",
            },
        ]
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                stock_order.trader.config.trade_db_path = f"{tmpdir}/trades.sqlite"
                with patch.object(
                    stock_order, "_prepare_strategy_attribution_sells",
                    return_value=(orders, [], []),
                ), patch.object(
                    stock_order._stock, "_prepare_strategy_attribution_sells",
                    return_value=(orders, [], []),
                ), patch.object(
                    dashboard, "_prepare_strategy_attribution_sells",
                    return_value=(orders, [], []),
                ), patch.object(stock_order, "_required_env_missing", return_value=[]), patch.object(
                    dashboard, "_required_env_missing", return_value=[]
                ), patch.object(
                    stock_order, "_auto_approval_enabled", return_value=False
                ), patch.object(stock_order, "_clear_balance_cache"):
                    result = stock_order.sell_all_strategy_attribution({"strategy_id": "ai_rebalance"})

                self.assertEqual(result["created_count"], 2)
                approvals = stock_order.get_approvals()["approvals"]
                self.assertEqual({item["symbol"] for item in approvals}, {"196170", "005930"})
                self.assertTrue(all(item["strategy_id"] == "ai_rebalance" for item in approvals))
        finally:
            stock_order.trader.config.trade_db_path = original_db_path

    def test_preflight_fails_closed_when_open_buy_cancellation_is_unconfirmed(self):
        with patch.object(stock_order, "_get_api", return_value=object()), patch.object(
            stock_order,
            "_cancel_open_buy_orders_before_liquidation",
            return_value=[{
                "broker_order_id": "B-17",
                "status": "cancel_failed",
                "message": "timeout",
            }],
        ), patch.object(stock_order, "_get_balance_data") as balance:
            with self.assertRaises(stock_order.HTTPException) as raised:
                stock_order._prepare_strategy_attribution_sells("ai_rebalance")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("B-17", str(raised.exception.detail))
        balance.assert_not_called()

    def test_preflight_uses_fresh_balance_and_reports_shared_and_residual_qty(self):
        parsed = {
            "holdings": [{
                "symbol": "005930",
                "name": "Samsung Electronics",
                "qty": 10,
                "sellable_qty": 3,
            }]
        }

        def attach(data):
            data["holdings"][0]["strategy_allocations"] = [{
                "strategy_id": "ai_rebalance",
                "strategy_name": "AI rebalance",
                "allocated_qty": 6,
            }]
            return data

        with patch.object(stock_order, "_get_api", return_value=object()), patch.object(
            stock_order, "_cancel_open_buy_orders_before_liquidation", return_value=[]
        ), patch.object(
            stock_order, "_get_balance_data", return_value={"fresh": True}
        ) as balance, patch.object(
            stock_order, "_parse_balance", return_value=parsed
        ), patch(
            "src.dashboard.routes.account._attach_holding_strategies", side_effect=attach
        ), patch.object(
            stock_order, "_unsubmitted_dashboard_sell_symbols", return_value=set()
        ):
            orders, skipped, canceled = stock_order._prepare_strategy_attribution_sells(
                "ai_rebalance"
            )

        balance.assert_called_once_with(ANY, allow_cache=False)
        self.assertEqual(skipped, [])
        self.assertEqual(canceled, [])
        self.assertEqual(orders[0]["qty"], 3)
        self.assertEqual(orders[0]["allocated_qty_snapshot"], 6)
        self.assertEqual(orders[0]["shared_qty_snapshot"], 4)
        self.assertEqual(orders[0]["expected_remaining_attribution"], 3)


if __name__ == "__main__":
    unittest.main()
