import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import src.dashboard as dashboard


class DashboardPlanViewRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signals_route = cls._find_route("/api/signals")
        cls.candidates_route = cls._find_route("/api/candidates")
        cls.execution_plan_route = cls._find_route("/api/execution-plan")

    @staticmethod
    def _find_route(path):
        for route in dashboard.app.routes:
            methods = getattr(route, "methods", set()) or set()
            if "GET" in methods and getattr(route, "path", "") == path:
                return route
        raise AssertionError(f"Missing GET route for {path}")

    def _call_route(self, route, *args, **kwargs):
        return asyncio.run(route.endpoint(*args, **kwargs))

    def test_execution_plan_shapes_runtime_plan_for_dashboard_view(self):
        expected_plan = [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "category": "position",
                "action": "sell",
                "qty": 1,
                "price": 71000,
                "reason": "take profit",
            },
            {
                "symbol": "000660",
                "name": "SK Hynix",
                "category": "candidate",
                "action": "buy",
                "qty": 2,
                "price": 120000,
                "reason": "score 4",
            },
        ]
        parsed_balance = {"cash": 500000, "total_eval": 2100000, "pnl": 15000}
        runtime_bundle = {
            "plan": expected_plan,
            "remaining_cash": 260000,
            "daily_loss_halt": True,
            "candidate_scan": {"scanned": 17, "scan_error": "yfinance timeout"},
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_get_api", return_value=MagicMock()))
            stack.enter_context(patch.object(dashboard, "_get_balance_data", return_value={"output1": [], "output2": [{}]}))
            stack.enter_context(patch.object(dashboard, "_parse_balance", return_value=parsed_balance))
            # DB-우선 read-through는 별도로 검증한다. 여기서는 builder의 shaping만 본다.
            stack.enter_context(
                patch.object(dashboard.core, "snapshot_read_through", side_effect=lambda kind, builder, **kw: builder())
            )
            build_runtime_plan = stack.enter_context(
                patch.object(dashboard.trader, "build_runtime_plan", return_value=runtime_bundle)
            )

            body = self._call_route(self.execution_plan_route)

        self.assertEqual(
            body,
            {
                "mode": "dashboard",
                "strategy_id": "seven_split",
                "plan": expected_plan,
                "cash": 500000,
                "remaining_cash": 260000,
                "total_eval": 2100000,
                "pnl": 15000,
                "daily_loss_halt": True,
                "scanned": 17,
                "scan_error": "yfinance timeout",
            },
        )
        build_runtime_plan.assert_called_once()

    def test_signals_shapes_plan_relevant_fields_and_defaults(self):
        fake_api = MagicMock()
        fake_api.get_daily.side_effect = [
            [{"stck_clpr": "71000"}],
            [{"stck_clpr": "120000"}],
        ]
        parsed_balance = {
            "holdings": [
                {
                    "symbol": "005930",
                    "name": "Samsung Electronics",
                    "qty": 3,
                    "price": 70000,
                    "rt": 12.5,
                    "_raw": {"pdno": "005930"},
                },
                {
                    "symbol": "000660",
                    "name": "SK Hynix",
                    "qty": 1,
                    "price": 118000,
                    "rt": -1.2,
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
            stack.enter_context(
                patch.object(
                    dashboard.trader,
                    "generate_signal",
                    side_effect=[
                        {
                            "action": "sell",
                            "qty": 1,
                            "price": 71000,
                            "reason": "take profit",
                            "indicators": {"rsi": 72, "strategy_score": 5, "macd_hist": 1.3},
                        },
                        {"indicators": {"sma20": 100000}},
                    ],
                )
            )
            # DB-우선 read-through는 별도로 검증한다. 여기서는 builder의 shaping만 본다.
            stack.enter_context(
                patch.object(dashboard.core, "snapshot_read_through", side_effect=lambda kind, builder, **kw: builder())
            )

            body = self._call_route(self.signals_route)

        self.assertEqual(
            body,
            {
                "signals": [
                    {
                        "strategy_id": "seven_split",
                        "symbol": "005930",
                        "name": "Samsung Electronics",
                        "qty": 3,
                        "price": 70000,
                        "rt": 12.5,
                        "action": "sell",
                        "signal_qty": 1,
                        "signal_price": 71000,
                        "reason": "take profit",
                        "rsi": 72,
                        "rsi2": None,
                        "sma20": None,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": None,
                        "strategy_score": 5,
                        "macd_hist": 1.3,
                    },
                    {
                        "strategy_id": "seven_split",
                        "symbol": "000660",
                        "name": "SK Hynix",
                        "qty": 1,
                        "price": 118000,
                        "rt": -1.2,
                        "action": "hold",
                        "signal_qty": 0,
                        "signal_price": 0,
                        "reason": "",
                        "rsi": None,
                        "rsi2": None,
                        "sma20": 100000,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": None,
                        "strategy_score": None,
                        "macd_hist": None,
                    },
                ]
            },
        )
        self.assertEqual(fake_api.get_daily.call_count, 2)

    def test_candidates_shapes_rows_and_persists_cache_when_scan_succeeds(self):
        fake_api = MagicMock()
        parsed_balance = {"cash": 900000, "holdings": [{"symbol": "005930"}]}
        scan_result = {
            "candidates": [
                {
                    "ticker": "000660",
                    "current_price": 120000,
                    "score": 4,
                    "reasons": ["rsi", "macd"],
                    "rsi": 33,
                },
                {
                    "ticker": "035420",
                    "name": "NAVER",
                    "current_price": 220000,
                    "score": 2,
                    "reasons": ["sma"],
                    "sma20": 215000,
                },
            ],
            "scan_summary": [{"ticker": "000660", "score": 4}, {"ticker": "035420", "score": 2}],
            "scanned": 9,
            "scan_error": None,
        }
        built_orders = [
            {
                "ticker": "000660",
                "quantity": 2,
                "limit_price": 119500,
                "estimated_cost": 239000,
            }
        ]

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_load_candidate_cache", return_value=None))
            stack.enter_context(patch.object(dashboard, "_get_api", return_value=fake_api))
            stack.enter_context(patch.object(dashboard, "_get_balance_data", return_value={"output1": [], "output2": [{}]}))
            stack.enter_context(patch.object(dashboard, "_parse_balance", return_value=parsed_balance))
            build_universe = stack.enter_context(
                patch.object(dashboard.trader, "build_scan_universe", return_value=["000660", "035420", "251270"])
            )
            stack.enter_context(patch.object(dashboard.trader, "find_candidates", return_value=scan_result))
            build_orders = stack.enter_context(
                patch.object(dashboard.trader, "build_orders", return_value=built_orders)
            )
            save_cache = stack.enter_context(patch.object(dashboard, "_save_candidate_cache"))

            body = self._call_route(self.candidates_route, min_score=2)

        self.assertEqual(
            body,
            {
                "candidates": [
                    {
                        "ticker": "000660",
                        "name": "000660",
                        "current_price": 120000,
                        "score": 4,
                        "reasons": ["rsi", "macd"],
                        "rsi": 33,
                        "rsi2": None,
                        "macd_hist": None,
                        "sma20": None,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": None,
                        "planned_qty": 2,
                        "limit_price": 119500,
                        "estimated_cost": 239000,
                        "universe_size": 3,
                    },
                    {
                        "ticker": "035420",
                        "name": "NAVER",
                        "current_price": 220000,
                        "score": 2,
                        "reasons": ["sma"],
                        "rsi": None,
                        "rsi2": None,
                        "macd_hist": None,
                        "sma20": 215000,
                        "sma60": None,
                        "bb_lo": None,
                        "bb_hi": None,
                        "planned_qty": 0,
                        "limit_price": 0,
                        "estimated_cost": 0,
                        "universe_size": 3,
                    },
                ],
                "universe_size": 3,
                "scanned": 9,
                "min_score": 2,
                "scan_summary": [{"ticker": "000660", "score": 4}, {"ticker": "035420", "score": 2}],
                "scan_error": None,
            },
        )
        build_universe.assert_called_once()
        build_orders.assert_called_once_with(scan_result["candidates"], fake_api.get_quote, 1, 900000)
        save_cache.assert_called_once_with(2, body["candidates"], scan_result["scan_summary"], 9)

    def test_candidates_returns_cached_payload_without_rebuilding_scan(self):
        cached_payload = {
            "candidates": [{"ticker": "000660", "planned_qty": 2}],
            "universe_size": 12,
            "scanned": 12,
            "min_score": 3,
            "scan_summary": [{"ticker": "000660", "score": 4}],
            "scan_error": None,
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_load_candidate_cache", return_value=cached_payload))
            build_universe = stack.enter_context(patch.object(dashboard.trader, "build_scan_universe"))
            find_candidates = stack.enter_context(patch.object(dashboard.trader, "find_candidates"))

            body = self._call_route(self.candidates_route, min_score=3)

        self.assertEqual(body, cached_payload)
        build_universe.assert_not_called()
        find_candidates.assert_not_called()


if __name__ == "__main__":
    unittest.main()
