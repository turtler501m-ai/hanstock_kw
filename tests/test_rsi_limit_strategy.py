import unittest
from unittest.mock import patch

from src.strategy.custom_rules.rsi_limit_strategy import CustomRSILimitStrategy
from src.strategy.seven_split import generate_signal


class RsiOversoldReboundStrategyTests(unittest.TestCase):
    def _evaluate(self, *, previous_rsi=34.0, current_rsi=36.0, breakout=True):
        strategy = CustomRSILimitStrategy()
        prices = [100.0 + index * 0.05 for index in range(500)]
        highs = [price + 0.2 for price in prices]
        highs[-2] = prices[-1] - 0.1 if breakout else prices[-1] + 1.0
        indicators = {
            "opens": [price - 0.1 for price in prices],
            "highs": highs,
            "lows": [price - 0.3 for price in prices],
            "volumes": [100.0] * 221 + [130.0],
        }
        rsi = [50.0] * len(prices)
        rsi[-5] = 24.0
        if current_rsi <= 30:
            rsi[-4:-2] = [25.0, 26.0]
        rsi[-2:] = [previous_rsi, current_rsi]
        ema = [90.0] * len(prices)
        ema[-21] = 91.0
        ema[-1] = 95.0
        with (
            patch.object(strategy, "_rsi_series", return_value=rsi),
            patch.object(strategy, "_ema_series", return_value=ema),
            patch.object(strategy, "_atr", return_value=1.0),
        ):
            score = strategy.calculate_score(prices, indicators)
        return score, indicators

    def test_balanced_entry_requires_recovery_and_price_breakout(self):
        score, indicators = self._evaluate()
        metadata = indicators["rsi_oversold_rebound"]
        self.assertGreaterEqual(score, 2.0)
        self.assertTrue(metadata["entry_ready"])
        self.assertEqual(metadata["phase"], "entry")
        self.assertIn(metadata["grade"], {"A", "B"})

    def test_oversold_without_recovery_is_setup_not_entry(self):
        score, indicators = self._evaluate(previous_rsi=24.0, current_rsi=27.0)
        self.assertEqual(score, 0.0)
        self.assertFalse(indicators["rsi_oversold_rebound"]["entry_ready"])

    def test_recovery_uses_demo_threshold_and_extended_window(self):
        strategy = CustomRSILimitStrategy()
        rsi = [50.0] * 30
        rsi[-10] = 34.0
        rsi[-9] = 36.0
        self.assertEqual(strategy._recovery_index(rsi), len(rsi) - 9)
        self.assertEqual(strategy.effective_config()["oversold_threshold"], 35.0)
        self.assertEqual(strategy.effective_config()["recovery_trigger_window_bars"], 10)

    def test_recovery_without_previous_high_breakout_can_enter_by_score(self):
        score, indicators = self._evaluate(breakout=False)
        self.assertGreaterEqual(score, 2.0)
        self.assertTrue(indicators["rsi_oversold_rebound"]["entry_ready"])
        self.assertFalse(indicators["rsi_oversold_rebound"]["price_confirmed"])

    def test_safety_filter_blocks_scored_setup(self):
        strategy = CustomRSILimitStrategy()
        prices = [100.0 + index * 0.05 for index in range(500)]
        indicators = {
            "opens": [price - 0.1 for price in prices],
            "highs": [price + 0.2 for price in prices],
            "lows": [price - 0.3 for price in prices],
            "volumes": [100.0] * len(prices),
        }
        rsi = [50.0] * len(prices)
        rsi[-2:] = [29.0, 36.0]
        falling_ema = [110.0] * len(prices)
        with (
            patch.object(strategy, "_rsi_series", return_value=rsi),
            patch.object(strategy, "_ema_series", return_value=falling_ema),
            patch.object(strategy, "_atr", return_value=1.0),
        ):
            score = strategy.calculate_score(prices, indicators)
        self.assertEqual(score, 0.0)
        self.assertFalse(indicators["rsi_oversold_rebound"]["safety_ready"])

    def test_position_uses_structural_stop(self):
        profile = self._profile({"atr": 2.0, "recent_swing_low": 96.5, "runner_exit": False})
        signal = self._signal(profile, current=96.0, average=100.0, quantity=8)
        self.assertEqual(signal["action"], "sell")
        self.assertEqual(signal["qty"], 8)
        self.assertIn("structural stop", signal["reason"])

    def test_position_takes_half_at_one_r(self):
        profile = self._profile({"atr": 1.0, "recent_swing_low": 98.5, "runner_exit": False})
        signal = self._signal(profile, current=102.0, average=100.0, quantity=8)
        self.assertEqual(signal["action"], "sell")
        self.assertEqual(signal["qty"], 4)
        self.assertIn("TP1", signal["reason"])

    def test_tp1_is_not_repeated_after_half_was_already_sold(self):
        profile = self._profile({"atr": 1.0, "recent_swing_low": 98.5, "runner_exit": False})
        signal = self._signal(profile, current=102.0, average=100.0, quantity=4, initial_quantity=8)
        self.assertEqual(signal["action"], "hold")
        self.assertIn("after TP1", signal["reason"])

    def test_runner_exits_when_rsi_falls_back_below_70(self):
        profile = self._profile({"atr": 1.0, "recent_swing_low": 98.5, "runner_exit": True})
        signal = self._signal(profile, current=110.0, average=100.0, quantity=2)
        self.assertEqual(signal["action"], "sell")
        self.assertEqual(signal["qty"], 2)
        self.assertIn("runner", signal["reason"])

    def _profile(self, metadata):
        return {
            "rsi": 50.0, "rsi2": 50.0, "sma20": 100.0, "sma60": 90.0,
            "bb_lo": 80.0, "bb_hi": 120.0, "score": 0.0, "macd_hist": 0.0,
            "macd_bull_cross": False, "macd_bear_cross": False,
            "sma_dead_cross": False, "reasons": [], "heikin_ashi_scalping": {},
            "rsi_oversold_rebound": metadata,
        }

    def _signal(self, profile, *, current, average, quantity, initial_quantity=None):
        stock = {
            "pdno": "005930", "prpr": current, "hldg_qty": quantity,
            "evlu_pfls_rt": (current / average - 1) * 100, "pchs_avg_pric": average,
        }
        daily = [{"stck_oprc": str(current - 1), "stck_hgpr": str(current + 1),
                  "stck_lwpr": str(current - 2), "stck_clpr": str(current), "acml_vol": "1000"}]
        with (
            patch("src.strategy.seven_split.calc_strategy_profile", return_value=profile),
            patch(
                "src.strategy.seven_split.update_position_peak",
                return_value={"peak_price": current, "initial_quantity": initial_quantity or quantity},
            ),
            patch("src.strategy.seven_split.trailing_stop_signal", return_value={"triggered": False}),
            patch(
                "src.strategy.seven_split.update_strategy_position_risk",
                return_value={"current_stop": average - 1.5 * float(profile["rsi_oversold_rebound"].get("atr", 0)), "initial_r": 1.5 * float(profile["rsi_oversold_rebound"].get("atr", 0))},
            ),
        ):
            return generate_signal(stock, daily, "rsi_limit_strategy")


if __name__ == "__main__":
    unittest.main()
