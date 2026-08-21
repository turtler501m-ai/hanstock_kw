import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import src.dashboard as dashboard


class DashboardHelperTests(unittest.TestCase):
    def test_daily_history_uses_shared_chart_cache_without_broker_call(self):
        api = MagicMock()
        cached = [
            {
                "date": f"2026-07-{day:02d}",
                "open": day,
                "high": day + 1,
                "low": day - 1,
                "close": day,
                "volume": day * 100,
            }
            for day in range(1, 21)
        ]

        with patch("src.db.repository.load_daily_charts", return_value=cached):
            rows = dashboard.stock_service.load_daily_history(api, "005930", n=60)

        api.get_daily.assert_not_called()
        self.assertEqual(rows[0]["stck_bsop_date"], "2026-07-20")
        self.assertEqual(rows[-1]["stck_clpr"], 1)

    def test_daily_history_fills_empty_cache_from_broker(self):
        api = MagicMock()
        api.get_daily.return_value = [{"stck_bsop_date": "20260821", "stck_clpr": "80000"}]

        with patch("src.db.repository.load_daily_charts", return_value=[]), patch(
            "src.db.repository.save_daily_charts"
        ) as save:
            rows = dashboard.stock_service.load_daily_history(api, "005930", n=60)

        self.assertEqual(rows, api.get_daily.return_value)
        api.get_daily.assert_called_once_with("005930", n=60)
        save.assert_called_once_with("005930", rows)

    def test_build_dashboard_signals_preserves_holding_order_and_defaults(self):
        api = MagicMock()
        api.get_daily.side_effect = [
            [{"stck_clpr": "80500"}],
            [{"stck_clpr": "178000"}],
        ]
        parsed = {
            "holdings": [
                {
                    "symbol": "005930",
                    "name": "Samsung Electronics",
                    "qty": 2,
                    "price": 78000,
                    "rt": 5.1,
                    "_raw": {"pdno": "005930"},
                },
                {
                    "symbol": "000660",
                    "name": "SK Hynix",
                    "qty": 1,
                    "price": 182000,
                    "rt": -2.4,
                    "_raw": {"pdno": "000660"},
                },
            ]
        }

        with patch(
            "src.db.repository.load_daily_charts",
            return_value=[],
        ), patch(
            "src.db.repository.save_daily_charts",
        ), patch.object(
            dashboard.trader,
            "generate_signal",
            side_effect=[
                {
                    "action": "sell",
                    "qty": 1,
                    "price": 80500,
                    "reason": "trim into strength",
                    "indicators": {"rsi": 74, "macd_hist": 1.7},
                },
                {"indicators": {"sma20": 176000, "bb_lo": 170500}},
            ],
        ) as generate_signal, patch(
            "src.dashboard.core._resolve_dashboard_strategy",
            return_value={"id": "seven_split"},
        ):
            rows = dashboard.build_dashboard_signals(api, parsed)

        self.assertEqual(
            rows,
            [
                {
                    "strategy_id": "seven_split",
                    "symbol": "005930",
                    "name": "Samsung Electronics",
                    "qty": 2,
                    "price": 78000,
                    "rt": 5.1,
                    "action": "sell",
                    "signal_qty": 1,
                    "signal_price": 80500,
                    "reason": "trim into strength",
                    "rsi": 74,
                    "rsi2": None,
                    "sma20": None,
                    "sma60": None,
                    "bb_lo": None,
                    "bb_hi": None,
                    "strategy_score": None,
                    "macd_hist": 1.7,
                },
                {
                    "strategy_id": "seven_split",
                    "symbol": "000660",
                    "name": "SK Hynix",
                    "qty": 1,
                    "price": 182000,
                    "rt": -2.4,
                    "action": "hold",
                    "signal_qty": 0,
                    "signal_price": 0,
                    "reason": "",
                    "rsi": None,
                    "rsi2": None,
                    "sma20": 176000,
                    "sma60": None,
                    "bb_lo": 170500,
                    "bb_hi": None,
                    "strategy_score": None,
                    "macd_hist": None,
                },
            ],
        )
        self.assertEqual(generate_signal.call_count, 2)
        self.assertEqual(api.get_daily.call_count, 2)

    def test_build_dashboard_candidates_maps_orders_by_ticker_and_keeps_candidate_order(self):
        api = MagicMock()
        parsed = {"cash": 850000, "holdings": [{"symbol": "005930"}]}
        universe = ["035420", "000660", "251270"]
        scan_result = {
            "candidates": [
                {
                    "ticker": "035420",
                    "name": "NAVER",
                    "current_price": 220000,
                    "score": 3,
                    "reasons": ["sma20 reclaim"],
                    "sma20": 215000,
                },
                {
                    "ticker": "000660",
                    "current_price": 121000,
                    "score": 5,
                    "reasons": ["rsi", "macd"],
                    "rsi": 31,
                    "rsi2": 29,
                    "macd_hist": 2.1,
                },
            ],
            "scan_summary": [{"ticker": "035420", "score": 3}, {"ticker": "000660", "score": 5}],
            "scanned": 14,
            "scan_error": None,
        }
        built_orders = [
            {
                "ticker": "000660",
                "quantity": 2,
                "limit_price": 120500,
                "estimated_cost": 241000,
            },
            {
                "ticker": "035420",
                "quantity": 1,
                "limit_price": 219000,
                "estimated_cost": 219000,
            },
        ]

        with ExitStack() as stack:
            build_universe = stack.enter_context(
                patch.object(dashboard.trader, "build_scan_universe", return_value=universe)
            )
            find_candidates = stack.enter_context(
                patch.object(dashboard.trader, "find_candidates", return_value=scan_result)
            )
            build_orders = stack.enter_context(
                patch.object(dashboard.trader, "build_orders", return_value=built_orders)
            )

            payload = dashboard.build_dashboard_candidates(api, parsed, min_score=2)

        self.assertEqual(
            payload,
            {
                "candidates": [
                    {
                        "ticker": "035420",
                        "name": "NAVER",
                        "current_price": 220000,
                        "score": 3,
                        "reasons": ["sma20 reclaim"],
                        "rsi": None,
                        "rsi2": None,
                        "macd_hist": None,
                        "sma20": 215000,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": None,
                        "planned_qty": 1,
                        "limit_price": 219000,
                        "estimated_cost": 219000,
                        "universe_size": 3,
                    },
                    {
                        "ticker": "000660",
                        "name": "000660",
                        "current_price": 121000,
                        "score": 5,
                        "reasons": ["rsi", "macd"],
                        "rsi": 31,
                        "rsi2": 29,
                        "macd_hist": 2.1,
                        "sma20": None,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": None,
                        "planned_qty": 2,
                        "limit_price": 120500,
                        "estimated_cost": 241000,
                        "universe_size": 3,
                    },
                ],
                "universe_size": 3,
                "scanned": 14,
                "min_score": 2,
                "scan_summary": [{"ticker": "035420", "score": 3}, {"ticker": "000660", "score": 5}],
                "scan_error": None,
            },
        )
        build_universe.assert_called_once_with(api, {"005930"})
        find_candidates.assert_called_once_with({"005930"}, universe=universe, min_score=2)
        args = build_orders.call_args.args
        self.assertEqual(args[0], scan_result["candidates"])
        self.assertTrue(callable(args[1]))
        self.assertEqual(args[2:], (1, 850000))

    def test_build_dashboard_candidates_uses_scan_price_when_live_quote_fails(self):
        api = MagicMock()
        api.get_quote.side_effect = RuntimeError("quote unavailable")
        parsed = {"cash": 300000, "holdings": []}
        scan_result = {
            "candidates": [{
                "ticker": "251270", "current_price": 10000,
                "score": 3, "reasons": ["test"],
            }],
            "scan_summary": [], "scanned": 1, "scan_error": None,
        }

        def build_orders(candidates, quote_provider, held_count, cash):
            self.assertEqual(quote_provider("251270"), {"current": 10000.0})
            return []

        with patch.object(
            dashboard.trader, "build_scan_universe", return_value=["251270"]
        ), patch.object(
            dashboard.trader, "find_candidates", return_value=scan_result
        ), patch.object(
            dashboard.trader, "build_orders", side_effect=build_orders
        ):
            result = dashboard.build_dashboard_candidates(api, parsed)

        self.assertEqual(result["scanned"], 1)

    def test_build_dashboard_candidates_keeps_scan_error_and_order_fallbacks(self):
        api = MagicMock()
        parsed = {"cash": 300000, "holdings": []}
        scan_result = {
            "candidates": [
                {
                    "ticker": "251270",
                    "current_price": 10450,
                    "score": 2,
                    "reasons": [],
                    "bb_hi": 10900,
                }
            ],
            "scan_summary": [],
            "scanned": 0,
            "scan_error": "market data unavailable",
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(dashboard.trader, "build_scan_universe", return_value=["251270", "114800"])
            )
            stack.enter_context(patch.object(dashboard.trader, "find_candidates", return_value=scan_result))
            stack.enter_context(patch.object(dashboard.trader, "build_orders", return_value=[]))

            payload = dashboard.build_dashboard_candidates(api, parsed, min_score=4)

        self.assertEqual(
            payload,
            {
                "candidates": [
                    {
                        "ticker": "251270",
                        "name": "251270",
                        "current_price": 10450,
                        "score": 2,
                        "reasons": [],
                        "rsi": None,
                        "rsi2": None,
                        "macd_hist": None,
                        "sma20": None,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": 10900,
                        "planned_qty": 0,
                        "limit_price": 0,
                        "estimated_cost": 0,
                        "universe_size": 2,
                    }
                ],
                "universe_size": 2,
                "scanned": 0,
                "min_score": 4,
                "scan_summary": [],
                "scan_error": "market data unavailable",
            },
        )


if __name__ == "__main__":
    unittest.main()
