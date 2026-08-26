from __future__ import annotations

import unittest

from src.trader import apply_market_regime_sizing


class TraderMarketRegimeSizingTests(unittest.TestCase):
    def test_scales_buy_quantity_and_cost_but_preserves_sell(self):
        plan = [
            {"action": "buy", "qty": 10, "estimated_cost": 100_000, "metadata": {}},
            {"action": "sell", "qty": 7, "estimated_cost": 70_000, "metadata": {}},
        ]
        result = apply_market_regime_sizing(plan, multiplier=0.5)
        self.assertEqual(result[0]["qty"], 5)
        self.assertEqual(result[0]["estimated_cost"], 50_000)
        self.assertEqual(result[1]["qty"], 7)

    def test_blocked_regime_zeroes_only_buy_and_records_reason(self):
        result = apply_market_regime_sizing(
            [{"action": "buy", "qty": 3, "metadata": {}}, {"action": "exit", "qty": 2}],
            multiplier=0,
            block_reason="market_regime_not_allowed",
        )
        self.assertEqual(result[0]["qty"], 0)
        self.assertEqual(
            result[0]["metadata"]["market_regime_sizing"]["block_reason"],
            "market_regime_not_allowed",
        )
        self.assertEqual(result[1]["qty"], 2)


if __name__ == "__main__":
    unittest.main()
