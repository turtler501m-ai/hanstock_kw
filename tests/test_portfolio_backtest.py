import unittest

from src.strategy.portfolio_backtest import simulate_target_portfolio


class PortfolioBacktestTests(unittest.TestCase):
    def test_costs_reduce_return_and_measure_actual_turnover(self):
        targets = [{"AAA": 1.0}, {"BBB": 1.0}, {"AAA": 1.0}]
        returns = [{"AAA": 0.02}, {"BBB": 0.02}, {"AAA": 0.02}]

        gross = simulate_target_portfolio(
            targets,
            returns,
            initial_capital=10_000,
            commission_bps=0,
            slippage_bps=0,
            market_impact_bps=0,
            rebalance_threshold=0,
        )
        net = simulate_target_portfolio(
            targets,
            returns,
            initial_capital=10_000,
            commission_bps=10,
            slippage_bps=10,
            market_impact_bps=10,
            sell_tax_bps=20,
            rebalance_threshold=0,
        )

        self.assertEqual(net["metrics"]["trade_count"], 5)
        self.assertEqual(net["metrics"]["rebalance_count"], 3)
        self.assertGreater(net["metrics"]["total_turnover_pct"], 400)
        self.assertGreater(net["costs"]["total_cost_amount"], 0)
        self.assertLess(net["metrics"]["total_return_pct"], gross["metrics"]["total_return_pct"])

    def test_rebalance_threshold_suppresses_small_target_changes(self):
        result = simulate_target_portfolio(
            [{"AAA": 0.50}, {"AAA": 0.51}, {"AAA": 0.49}],
            [{"AAA": 0.0}, {"AAA": 0.0}, {"AAA": 0.0}],
            initial_capital=10_000,
            commission_bps=3,
            slippage_bps=5,
            market_impact_bps=2,
            rebalance_threshold=0.02,
        )

        self.assertEqual(result["metrics"]["trade_count"], 1)
        self.assertEqual(result["metrics"]["rebalance_count"], 1)
        self.assertEqual(result["metrics"]["buy_turnover_pct"], 50.0)

    def test_no_activity_does_not_receive_fabricated_performance(self):
        result = simulate_target_portfolio(
            [{}, {}],
            [{}, {}],
            initial_capital=10_000,
            commission_bps=3,
            slippage_bps=5,
            market_impact_bps=2,
        )

        self.assertEqual(result["metrics"]["win_rate"], 0.0)
        self.assertEqual(result["metrics"]["profit_factor"], 0.0)
        self.assertEqual(result["metrics"]["trade_count"], 0)


if __name__ == "__main__":
    unittest.main()
