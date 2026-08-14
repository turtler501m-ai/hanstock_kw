import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import src.dashboard as dashboard


class DashboardSignalCandidateAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signals_route = cls._find_route("/api/signals")
        cls.candidates_route = cls._find_route("/api/candidates")

    @staticmethod
    def _find_route(path):
        for route in dashboard.app.routes:
            methods = getattr(route, "methods", set()) or set()
            if "GET" in methods and getattr(route, "path", "") == path:
                return route
        raise AssertionError(f"Missing GET route for {path}")

    def _call_route(self, route, *args, **kwargs):
        return asyncio.run(route.endpoint(*args, **kwargs))

    def test_signals_route_preserves_holding_order_and_plan_friendly_defaults(self):
        fake_api = MagicMock()
        fake_api.get_daily.side_effect = [
            [{"stck_clpr": "80500"}],
            [{"stck_clpr": "178000"}],
        ]
        parsed_balance = {
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

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_get_api", return_value=fake_api))
            stack.enter_context(patch.object(dashboard, "_get_balance_data", return_value={"output1": [], "output2": [{}]}))
            stack.enter_context(patch.object(dashboard, "_parse_balance", return_value=parsed_balance))
            stack.enter_context(patch("src.dashboard.core._resolve_dashboard_strategy", return_value={"id": "seven_split"}))
            generate_signal = stack.enter_context(
                patch.object(
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
                        {
                            "indicators": {"sma20": 176000, "bb_lo": 170500},
                        },
                    ],
                )
            )
            # DB-우선 read-through는 별도로 검증한다. 여기서는 builder의 shaping만 본다.
            stack.enter_context(
                patch.object(dashboard.core, "snapshot_read_through", side_effect=lambda kind, builder, **kw: builder())
            )

            body = self._call_route(self.signals_route)

        self.assertEqual([row["symbol"] for row in body["signals"]], ["005930", "000660"])
        self.assertEqual(
            body,
            {
                "signals": [
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
                ]
            },
        )
        self.assertEqual(generate_signal.call_count, 2)

    def test_candidates_route_uses_default_min_score_and_preserves_candidate_order(self):
        fake_api = MagicMock()
        parsed_balance = {"cash": 850000, "holdings": [{"symbol": "005930"}]}
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
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_load_candidate_cache", return_value=None))
            stack.enter_context(patch.object(dashboard, "_get_api", return_value=fake_api))
            stack.enter_context(patch.object(dashboard, "_get_balance_data", return_value={"output1": [], "output2": [{}]}))
            stack.enter_context(patch.object(dashboard, "_parse_balance", return_value=parsed_balance))
            stack.enter_context(
                patch.object(dashboard.trader, "build_scan_universe", return_value=["035420", "000660", "251270"])
            )
            find_candidates = stack.enter_context(
                patch.object(dashboard.trader, "find_candidates", return_value=scan_result)
            )
            stack.enter_context(patch.object(dashboard.trader, "build_orders", return_value=built_orders))
            save_cache = stack.enter_context(patch.object(dashboard, "_save_candidate_cache"))

            body = self._call_route(self.candidates_route)

        self.assertEqual([row["ticker"] for row in body["candidates"]], ["035420", "000660"])
        self.assertEqual(
            body,
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
        find_candidates.assert_called_once_with({"005930"}, universe=["035420", "000660", "251270"], min_score=2)
        save_cache.assert_called_once_with(2, body["candidates"], scan_result["scan_summary"], 14)

    def test_candidates_route_keeps_scan_fallbacks_when_scan_does_not_produce_cacheable_results(self):
        fake_api = MagicMock()
        parsed_balance = {"cash": 300000, "holdings": []}
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
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_load_candidate_cache", return_value=None))
            stack.enter_context(patch.object(dashboard, "_get_api", return_value=fake_api))
            stack.enter_context(patch.object(dashboard, "_get_balance_data", return_value={"output1": [], "output2": [{}]}))
            stack.enter_context(patch.object(dashboard, "_parse_balance", return_value=parsed_balance))
            stack.enter_context(patch.object(dashboard.trader, "build_scan_universe", return_value=["251270", "114800"]))
            stack.enter_context(patch.object(dashboard.trader, "find_candidates", return_value=scan_result))
            stack.enter_context(patch.object(dashboard.trader, "build_orders", return_value=[]))
            save_cache = stack.enter_context(patch.object(dashboard, "_save_candidate_cache"))

            body = self._call_route(self.candidates_route, min_score=4)

        self.assertEqual(
            body,
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
        save_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
