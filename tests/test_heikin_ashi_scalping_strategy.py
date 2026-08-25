import unittest
from unittest.mock import patch

from src.strategy.heikin_ashi_scalping import HeikinAshiScalpingStrategy
from src.strategy.heikin_ashi_scalping.strategy import Candle
from src.strategy.seven_split import calc_strategy_profile, generate_signal


class HeikinAshiScalpingStrategyTests(unittest.TestCase):
    def _calculate(self, colors, *, breakout=True):
        strategy = HeikinAshiScalpingStrategy()
        prices = [100.0 + index * 0.1 for index in range(500)]
        candles = [Candle(price - 0.2, price + 0.3, price - 0.3, price) for price in prices]
        alpha = [Candle(100, 101, 99, 100.8) for _ in prices]
        for index, color in enumerate(colors, start=len(alpha) - len(colors)):
            alpha[index] = Candle(100, 101, 99, 100.8) if color == "bull" else Candle(100.8, 101, 99, 100)
        signal_high = prices[-1] - 0.1 if breakout else prices[-1] + 1
        candles[-2] = Candle(prices[-2], signal_high, prices[-2] - 0.3, prices[-2])
        ema = [90.0] * len(prices)
        ema[-4:] = [95.0, 96.0, 97.0, 98.0]
        indicators = {"highs": [c.high for c in candles], "lows": [c.low for c in candles]}
        with (
            patch.object(strategy, "_heikin_ashi", side_effect=[candles, alpha]),
            patch.object(strategy, "_ema_series", return_value=ema),
            patch.object(strategy, "_directional_indicators", return_value=(25.0, 30.0, 10.0)),
            patch.object(strategy, "_atr", return_value=1.5),
        ):
            score = strategy.calculate_score(prices, indicators)
        return score, indicators

    def test_long_requires_transition_confirmation_and_breakout(self):
        score, indicators = self._calculate(["bear", "bull", "bull"])
        metadata = indicators["heikin_ashi_scalping"]
        self.assertGreaterEqual(score, 4.5)
        self.assertTrue(metadata["long_setup"])
        self.assertEqual(metadata["direction"], "long")
        self.assertEqual(metadata["minimum_entry_score"], 2.5)
        self.assertIn("price_confirmation", metadata["score_components"])
        self.assertGreater(metadata["target_1r"], metadata["entry"])

    def test_single_color_change_does_not_enter(self):
        score, indicators = self._calculate(["bear", "bear", "bull"])
        self.assertEqual(score, 0.0)
        self.assertFalse(indicators["heikin_ashi_scalping"]["long_setup"])

    def test_breakout_adds_quality_but_is_not_required_in_demo(self):
        confirmed_score, _ = self._calculate(["bear", "bull", "bull"], breakout=True)
        score, indicators = self._calculate(["bear", "bull", "bull"], breakout=False)
        self.assertGreaterEqual(score, 2.5)
        self.assertLess(score, confirmed_score)
        self.assertTrue(indicators["heikin_ashi_scalping"]["long_setup"])
        self.assertFalse(indicators["heikin_ashi_scalping"]["price_confirmed"])

    def test_demo_scoring_uses_extended_reversal_window(self):
        strategy = HeikinAshiScalpingStrategy()
        self.assertEqual(strategy.trigger_window, 7)
        self.assertEqual(strategy.effective_config()["minimum_entry_score"], 2.5)

    def test_ema200_safety_filter_still_blocks_entry(self):
        strategy = HeikinAshiScalpingStrategy()
        prices = [100.0 + index * 0.1 for index in range(500)]
        candles = [Candle(price - 0.2, price + 0.3, price - 0.3, price) for price in prices]
        alpha = [Candle(100, 101, 99, 100.8) for _ in prices]
        alpha[-3] = Candle(100.8, 101, 99, 100)
        indicators = {"highs": [c.high for c in candles], "lows": [c.low for c in candles]}
        with (
            patch.object(strategy, "_heikin_ashi", side_effect=[candles, alpha]),
            patch.object(strategy, "_ema_series", return_value=[200.0] * len(prices)),
            patch.object(strategy, "_directional_indicators", return_value=(25.0, 30.0, 10.0)),
            patch.object(strategy, "_atr", return_value=1.5),
        ):
            score = strategy.calculate_score(prices, indicators)
        self.assertEqual(score, 0.0)
        self.assertFalse(indicators["heikin_ashi_scalping"]["safety_ready"])

    def test_short_is_metadata_only_for_spot_order_safety(self):
        strategy = HeikinAshiScalpingStrategy()
        prices = [140.0 - index * 0.1 for index in range(500)]
        candles = [Candle(price + 0.2, price + 0.3, price - 0.3, price) for price in prices]
        candles[-2] = Candle(prices[-2], prices[-2] + 0.3, prices[-1] + 0.1, prices[-2])
        alpha = [Candle(100.8, 101, 99, 100) for _ in prices]
        alpha[-3] = Candle(100, 101, 99, 100.8)
        ema = [130.0] * len(prices)
        ema[-4:] = [125.0, 124.0, 123.0, 122.0]
        indicators = {}
        with (
            patch.object(strategy, "_heikin_ashi", side_effect=[candles, alpha]),
            patch.object(strategy, "_ema_series", return_value=ema),
            patch.object(strategy, "_directional_indicators", return_value=(25.0, 10.0, 30.0)),
            patch.object(strategy, "_atr", return_value=1.5),
        ):
            score = strategy.calculate_score(prices, indicators)
        self.assertEqual(score, 0.0)
        self.assertTrue(indicators["heikin_ashi_scalping"]["short_setup"])
        self.assertEqual(indicators["heikin_ashi_scalping"]["direction"], "short")

    def test_custom_metadata_flows_into_strategy_profile(self):
        class FakeStrategy:
            def calculate_score(self, _prices, indicators):
                indicators["heikin_ashi_scalping"] = {"direction": "long"}
                indicators["custom_reasons"] = ["confirmed"]
                return 5.0

        with patch("src.db.repository.get_custom_strategy_instance", return_value=FakeStrategy()):
            profile = calc_strategy_profile(
                [float(index) for index in range(1, 206)],
                strategy_model="heikin_ashi_scalping_strategy",
            )
        self.assertEqual(profile["score"], 5.0)
        self.assertEqual(profile["heikin_ashi_scalping"]["direction"], "long")

    def test_first_bearish_alpha_candle_exits_full_position(self):
        profile = {
            "rsi": 50.0, "rsi2": 50.0, "sma20": 100.0, "sma60": 90.0,
            "bb_lo": 80.0, "bb_hi": 120.0, "score": 0.0, "macd_hist": 0.0,
            "macd_bull_cross": False, "macd_bear_cross": False,
            "sma_dead_cross": False, "reasons": [],
            "heikin_ashi_scalping": {"exit_long": True, "exit_long_confirmed": False},
        }
        stock = {"pdno": "005930", "prpr": 100, "hldg_qty": 7, "evlu_pfls_rt": 1.0, "pchs_avg_pric": 99}
        daily_data = [{"stck_clpr": "100", "stck_hgpr": "101", "acml_vol": "1000"}]
        with (
            patch("src.strategy.seven_split.calc_strategy_profile", return_value=profile),
            patch("src.strategy.seven_split.update_position_peak", return_value={"peak_price": 100}),
            patch("src.strategy.seven_split.trailing_stop_signal", return_value={"triggered": False}),
            patch("src.strategy.seven_split.update_strategy_position_risk", return_value={"current_stop": 90, "initial_r": 10}),
        ):
            signal = generate_signal(stock, daily_data, "heikin_ashi_scalping_strategy")
        self.assertEqual(signal["action"], "sell")
        self.assertEqual(signal["qty"], 4)
        self.assertEqual(signal["price"], 0)


if __name__ == "__main__":
    unittest.main()
