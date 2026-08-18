import unittest
from unittest.mock import patch

import pandas as pd

from src.strategy.custom_rules.plunge_bounce_strategy import PlungeBounceStrategy


class PlungeBounceStrategyTests(unittest.TestCase):
    def setUp(self):
        PlungeBounceStrategy._index_cache = {}
        PlungeBounceStrategy._last_cache_time = None

    @patch("src.strategy.custom_rules.plunge_bounce_strategy.yf.download")
    @patch("src.online_access.require_online_access")
    def test_raw_krx_symbol_uses_kospi_index(self, _access, download):
        download.return_value = pd.DataFrame({"Close": list(range(1, 202))})
        strategy = PlungeBounceStrategy.__new__(PlungeBounceStrategy)

        self.assertTrue(strategy._is_index_above_sma("005930"))
        download.assert_called_once()
        self.assertEqual(download.call_args.args[0], "^KS11")

    @patch("src.strategy.custom_rules.plunge_bounce_strategy.yf.download")
    @patch("src.online_access.require_online_access")
    def test_invalid_index_values_do_not_reject_every_stock(self, _access, download):
        download.return_value = pd.DataFrame({"Close": [float("nan")] * 200})
        strategy = PlungeBounceStrategy.__new__(PlungeBounceStrategy)

        self.assertTrue(strategy._is_index_above_sma("005930"))

    @patch("src.db.repository.get_watchlist_setting")
    def test_zero_max_transaction_value_disables_upper_bound(self, setting):
        values = {
            "PLUNGE_DEVIATION_THRESHOLD": "-15",
            "PLUNGE_RSI_THRESHOLD": "30",
            "PLUNGE_VOL_RATIO_THRESHOLD": "1.4",
            "PLUNGE_MIN_VAL_KRW": "1000000",
            "PLUNGE_MAX_VAL_KRW": "0",
            "PLUNGE_INDEX_FILTER_ENABLED": "0",
        }
        setting.side_effect = lambda key, default: values.get(key, default)
        strategy = PlungeBounceStrategy()
        indicators = {
            "symbol": "005930",
            "rsi": 20,
            "volumes": [10_000] * 20 + [20_000],
        }

        self.assertEqual(strategy.calculate_score([100] * 22 + [80], indicators), 5.0)


if __name__ == "__main__":
    unittest.main()
