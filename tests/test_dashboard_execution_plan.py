import unittest
import asyncio
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import src.dashboard as dashboard


class DashboardExecutionPlanApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.route = cls._find_execution_plan_route()

    @staticmethod
    def _find_execution_plan_route():
        candidates = []
        for route in dashboard.app.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            if "GET" in methods and "execution-plan" in path and "ai-stock" not in path:
                candidates.append(route)
        if not candidates:
            raise AssertionError("No GET dashboard execution-plan route is registered on src.dashboard.app")
        candidates.sort(key=lambda route: (not route.path.startswith("/api/"), route.path))
        return candidates[0]

    def _call_route(self):
        endpoint = self.route.endpoint
        return asyncio.run(endpoint())

    def _expected_plan(self):
        return [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "category": "position",
                "action": "sell",
                "qty": 1,
                "price": 70000,
                "reason": "take profit",
                "score": None,
                "reasons": [],
                "indicators": {"rsi": 71},
                "metadata": {"return_pct": 12.5},
            },
            {
                "symbol": "000660",
                "name": "SK Hynix",
                "category": "candidate",
                "action": "buy",
                "qty": 2,
                "price": 120000,
                "reason": "new buy score=4 (rsi, macd)",
                "score": 4,
                "reasons": ["rsi", "macd"],
                "indicators": {"rsi": 33},
                "metadata": {"scan_rank": 1},
            },
        ]

    def _expected_response(self):
        return {
            "mode": "dashboard",
            "plan": self._expected_plan(),
            "results": [],
            "cash": 500000,
            "total_eval": 1500000,
            "pnl": 10000,
        }

    def _builder_patchers(self, return_value=None, side_effect=None):
        names = [
            "build_dashboard_execution_plan",
            "build_execution_plan_for_dashboard",
            "build_execution_plan_snapshot",
            "generate_dashboard_execution_plan",
            "generate_execution_plan",
            "get_dashboard_execution_plan",
            "get_execution_plan",
        ]
        return [
            patch.object(dashboard.trader, name, return_value=return_value, side_effect=side_effect, create=True)
            for name in names
        ]

    def _patch_success_flow(self):
        expected_plan = self._expected_plan()
        expected_response = self._expected_response()
        fake_api = MagicMock()
        fake_api.get_daily.return_value = [{"stck_clpr": "70000", "stck_hgpr": "71000", "acml_vol": "1000"}]
        fake_api.get_quote.return_value = 120000

        parsed_balance = {
            "cash": expected_response["cash"],
            "total_eval": expected_response["total_eval"],
            "pnl": expected_response["pnl"],
            "holdings": [
                {
                    "symbol": "005930",
                    "name": "Samsung Electronics",
                    "qty": 3,
                    "price": 70000,
                    "rt": 12.5,
                    "value": 210000,
                    "_raw": {"pdno": "005930"},
                }
            ],
        }

        return [
            patch.object(dashboard, "_required_env_missing", return_value=[]),
            patch.object(dashboard, "_get_api", return_value=fake_api),
            patch.object(dashboard, "_get_balance_data", return_value={"output1": [], "output2": [{}]}),
            patch.object(dashboard, "_parse_balance", return_value=parsed_balance),
            patch.object(
                dashboard.trader,
                "generate_signal",
                return_value={
                    "action": "sell",
                    "qty": 1,
                    "price": 70000,
                    "reason": "take profit",
                    "indicators": {"rsi": 71},
                },
            ),
            patch.object(dashboard.trader, "build_scan_universe", return_value=["000660"]),
            patch.object(
                dashboard.trader,
                "find_candidates",
                return_value={
                    "candidates": [
                        {
                            "ticker": "000660",
                            "name": "SK Hynix",
                            "score": 4,
                            "reasons": ["rsi", "macd"],
                            "current_price": 120000,
                        }
                    ],
                    "scan_summary": [{"ticker": "000660", "score": 4, "rsi": 33, "reasons": ["rsi", "macd"]}],
                    "scanned": 1,
                    "min_score": 2,
                },
            ),
            patch.object(
                dashboard.trader,
                "build_orders",
                return_value=[
                    {
                        "ticker": "000660",
                        "quantity": 2,
                        "limit_price": 120000,
                        "estimated_cost": 240000,
                        "score": 4,
                        "reasons": ["rsi", "macd"],
                    }
                ],
            ),
            patch.object(dashboard.trader, "signal_to_plan_row", return_value=expected_plan[0]),
            patch.object(dashboard.trader, "candidate_order_to_plan_row", return_value=expected_plan[1]),
            patch.object(dashboard.trader, "build_execution_plan", return_value=expected_plan),
            patch.object(dashboard.trader, "run", return_value=expected_response),
            # DB-우선 read-through는 별도로 검증한다. 여기서는 builder 경로만 본다.
            patch.object(dashboard.core, "snapshot_read_through", side_effect=lambda kind, builder, **kw: builder()),
            *self._builder_patchers(return_value=expected_response),
        ]

    def test_execution_plan_returns_plan_shape(self):
        expected_plan = self._expected_plan()
        expected_response = self._expected_response()

        with ExitStack() as stack:
            for patcher in self._patch_success_flow():
                stack.enter_context(patcher)

            body = self._call_route()

        self.assertIsInstance(body, dict)
        self.assertIn("plan", body)
        self.assertEqual(body["plan"], expected_plan)

        if "mode" in body:
            self.assertEqual(body["mode"], expected_response["mode"])
        if "cash" in body:
            self.assertEqual(body["cash"], expected_response["cash"])
        if "total_eval" in body:
            self.assertEqual(body["total_eval"], expected_response["total_eval"])
        if "pnl" in body:
            self.assertEqual(body["pnl"], expected_response["pnl"])

    def test_execution_plan_returns_503_when_required_env_missing(self):
        with patch.object(dashboard, "_required_env_missing", return_value=["KISTOCK_APP_KEY"]):
            with self.assertRaises(HTTPException) as exc:
                self._call_route()

        self.assertEqual(exc.exception.status_code, 503)
        self.assertIn("KISTOCK_APP_KEY", exc.exception.detail)

    def test_execution_plan_returns_502_when_builder_fails(self):
        error = RuntimeError("execution plan exploded")

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "_required_env_missing", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_get_api", side_effect=error))
            stack.enter_context(patch.object(dashboard.trader, "run", side_effect=error))
            # builder 실패가 502로 매핑되는지를 본다(스냅샷 폴백은 별도 검증).
            stack.enter_context(
                patch.object(dashboard.core, "snapshot_read_through", side_effect=lambda kind, builder, **kw: builder())
            )
            for patcher in self._builder_patchers(side_effect=error):
                stack.enter_context(patcher)

            with self.assertRaises(HTTPException) as exc:
                self._call_route()

        self.assertEqual(exc.exception.status_code, 502)
        self.assertIn("execution plan exploded", exc.exception.detail)


if __name__ == "__main__":
    unittest.main()
