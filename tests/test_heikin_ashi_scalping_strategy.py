import unittest

from src.strategy.heikin_ashi_scalping import HeikinAshiScalpingStrategy
from src.strategy.seven_split import calc_strategy_profile


class HeikinAshiScalpingStrategyTests(unittest.TestCase):
    def _bullish_reversal_data(self):
        prices = [
            120, 118, 116, 114, 112, 110, 108, 106,
            104, 102, 100, 99, 98, 97, 96, 95,
            94, 93, 92, 91, 90, 90.5, 90.2, 90.1,
            90.3, 91, 92, 93.5, 95, 97, 99, 101,
        ]
        highs = [price + 1 for price in prices]
        lows = [price - 1 for price in prices]
        opens = [prices[idx - 1] if idx else prices[idx] for idx in range(len(prices))]
        volumes = [100] * 25 + [150] * 7
        return prices, opens, highs, lows, volumes

    def test_calculate_score_adds_alpha_heikin_ashi_metadata(self):
        prices, opens, highs, lows, volumes = self._bullish_reversal_data()
        indicators = {
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
        }

        score = HeikinAshiScalpingStrategy().calculate_score(prices, indicators)

        self.assertGreater(score, 0)
        self.assertIn("custom_reasons", indicators)
        self.assertIn("heikin_ashi_scalping", indicators)
        self.assertEqual(indicators["heikin_ashi_scalping"]["alpha_color"], "bull")
        self.assertGreater(indicators["heikin_ashi_scalping"]["target_2r"], prices[-1])

    def test_custom_strategy_reasons_flow_into_strategy_profile(self):
        prices, _opens, highs, _lows, volumes = self._bullish_reversal_data()

        profile = calc_strategy_profile(
            prices,
            highs,
            volumes,
            strategy_model="heikin_ashi_scalping_strategy",
            symbol="AAPL",
        )

        self.assertGreater(profile["score"], 0)
        self.assertTrue(
            any("하이킨아시" in reason for reason in profile["reasons"])
        )


if __name__ == "__main__":
    unittest.main()
