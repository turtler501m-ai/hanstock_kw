import ast
import json
import pathlib
import unittest
from unittest.mock import patch

import src.dashboard as dashboard


ROOT = pathlib.Path(__file__).resolve().parents[1]
ALGO_PATH = ROOT / "src" / "integrations" / "quantconnect" / "mnq_paper_auto" / "main.py"
CONFIG_PATH = ROOT / "src" / "integrations" / "quantconnect" / "mnq_paper_auto" / "config.json"
DOC_PATH = next((ROOT / "doc").glob("S1.*.md"))


class QuantConnectMnqAlgorithmTests(unittest.TestCase):
    @staticmethod
    def _find_route(path, method="GET"):
        for route in dashboard.app.routes:
            methods = getattr(route, "methods", set()) or set()
            if method in methods and getattr(route, "path", "") == path:
                return route
        raise AssertionError(f"Missing {method} route for {path}")

    def test_algorithm_is_valid_python_source(self):
        ast.parse(ALGO_PATH.read_text(encoding="utf-8"))

    def test_algorithm_targets_mnq_paper_trading_surface(self):
        source = ALGO_PATH.read_text(encoding="utf-8")

        self.assertIn("Futures.Indices.MICRO_NASDAQ_100_E_MINI", source)
        self.assertIn("self.add_future", source)
        self.assertIn("self.market_order", source)
        self.assertIn("def on_command", source)
        self.assertIn("_roll_active_contract", source)
        self.assertIn("MAX_CONTRACTS", source)
        self.assertNotIn("KIS", source)
        self.assertNotIn("ENABLE_LIVE_TRADING", source)

    def test_config_matches_algorithm_entrypoint(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(config["algorithm-language"], "Python")
        self.assertEqual(config["algorithm-location"], "main.py")
        self.assertEqual(config["parameters"]["MAX_CONTRACTS"], "3")

    def test_documentation_records_feasibility_and_limits(self):
        doc = DOC_PATH.read_text(encoding="utf-8")

        self.assertIn("live-paper", doc)
        self.assertIn("QuantConnect Paper Trading", doc)
        self.assertIn("MICRO_NASDAQ_100_E_MINI", doc)

    def test_dashboard_status_endpoint_reports_project_readiness(self):
        route = self._find_route("/api/quantconnect/mnq/status")
        original_cache = dashboard.QUANTCONNECT_AUTH_CACHE
        dashboard.QUANTCONNECT_AUTH_CACHE = ROOT / ".runtime" / "test_quantconnect_auth_cache.json"
        dashboard.QUANTCONNECT_AUTH_CACHE.unlink(missing_ok=True)

        try:
            payload = route.endpoint()
        finally:
            dashboard.QUANTCONNECT_AUTH_CACHE.unlink(missing_ok=True)
            dashboard.QUANTCONNECT_AUTH_CACHE = original_cache

        self.assertTrue(payload["feasible"])
        self.assertTrue(payload["project_ready"])
        self.assertIn("auth", payload)
        self.assertEqual(payload["algorithm"]["symbol"], "MNQ")
        self.assertEqual(payload["algorithm"]["brokerage"], "QuantConnect Paper Trading")
        self.assertIn("orders", payload)

    def test_dashboard_order_endpoint_requires_project_id(self):
        route = self._find_route("/api/quantconnect/mnq/order", method="POST")

        with patch.object(
            dashboard,
            "_quantconnect_credentials",
            return_value=dashboard.QuantConnectCredentials("123", "token", ""),
        ):
            with self.assertRaises(dashboard.HTTPException) as exc:
                route.endpoint({"side": "buy", "quantity": 1})

        self.assertEqual(exc.exception.status_code, 400)

    def test_dashboard_order_endpoint_sends_live_command(self):
        route = self._find_route("/api/quantconnect/mnq/order", method="POST")

        with patch.object(
            dashboard,
            "_quantconnect_credentials",
            return_value=dashboard.QuantConnectCredentials("123", "token", "456"),
        ), patch.object(
            dashboard,
            "_quantconnect_cloud_snapshot",
            return_value={"live": {"status": "Running"}},
        ), patch.object(dashboard.QuantConnectAPI, "create_live_command", return_value={"success": True}) as command:
            payload = route.endpoint({"side": "sell", "quantity": 1})

        self.assertTrue(payload["success"])
        self.assertEqual(payload["command"]["side"], "sell")
        command.assert_called_once()

    def test_dashboard_order_endpoint_rejects_stopped_live_instance(self):
        route = self._find_route("/api/quantconnect/mnq/order", method="POST")

        with patch.object(
            dashboard,
            "_quantconnect_credentials",
            return_value=dashboard.QuantConnectCredentials("123", "token", "456"),
        ), patch.object(
            dashboard,
            "_quantconnect_cloud_snapshot",
            return_value={"live": {"status": "Stopped"}},
        ), patch.object(dashboard.QuantConnectAPI, "create_live_command") as command:
            with self.assertRaises(dashboard.HTTPException) as exc:
                route.endpoint({"side": "buy", "quantity": 1})

        self.assertEqual(exc.exception.status_code, 409)
        self.assertIn("no running Paper Live instance", exc.exception.detail)
        command.assert_not_called()

    def test_dashboard_order_endpoint_allows_three_contracts(self):
        route = self._find_route("/api/quantconnect/mnq/order", method="POST")

        with patch.object(
            dashboard,
            "_quantconnect_credentials",
            return_value=dashboard.QuantConnectCredentials("123", "token", "456"),
        ), patch.object(
            dashboard,
            "_quantconnect_cloud_snapshot",
            return_value={"live": {"status": "Running"}},
        ), patch.object(dashboard.QuantConnectAPI, "create_live_command", return_value={"success": True}):
            payload = route.endpoint({"side": "buy", "quantity": 3})

        self.assertTrue(payload["success"])
        self.assertEqual(payload["command"]["quantity"], 3)

    def test_dashboard_order_endpoint_rejects_more_than_three_contracts(self):
        route = self._find_route("/api/quantconnect/mnq/order", method="POST")

        with patch.object(
            dashboard,
            "_quantconnect_credentials",
            return_value=dashboard.QuantConnectCredentials("123", "token", "456"),
        ):
            with self.assertRaises(dashboard.HTTPException) as exc:
                route.endpoint({"side": "buy", "quantity": 4})

        self.assertEqual(exc.exception.status_code, 400)

    def test_dashboard_deploy_endpoint_creates_paper_live_algorithm(self):
        route = self._find_route("/api/quantconnect/mnq/deploy", method="POST")

        with patch.object(
            dashboard,
            "_quantconnect_credentials",
            return_value=dashboard.QuantConnectCredentials("123", "token", "456"),
        ), patch.object(
            dashboard.QuantConnectAPI,
            "read_project_nodes",
            return_value={"success": True, "nodes": {"live": [{"id": "node-1", "active": True, "busy": False, "name": "Live Node"}]}},
        ) as nodes, patch.object(
            dashboard.QuantConnectAPI,
            "create_compile",
            return_value={"success": True, "state": "BuildSuccess", "compileId": "compile-1"},
        ) as compile_, patch.object(
            dashboard.QuantConnectAPI,
            "create_live_algorithm",
            return_value={"success": True, "deployId": "L-123"},
        ) as live, patch.object(
            dashboard,
            "_quantconnect_cloud_snapshot",
            return_value={"live": {"status": "Running"}},
        ):
            payload = route.endpoint({})

        self.assertTrue(payload["success"])
        self.assertEqual(payload["deploy_id"], "L-123")
        nodes.assert_called_once()
        compile_.assert_called_once()
        live.assert_called_once()

    def test_futures_dashboard_has_required_tabs(self):
        """Verify required futures dashboard tabs."""
        template = (ROOT / "web" / "templates" / "futures_signals.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "static" / "js" / "futures_signals.js").read_text(encoding="utf-8")

        self.assertIn("futures_tab_overview.html", template)
        self.assertIn("futures_tab_signals.html", template)
        self.assertIn("futures_tab_mock_performance.html", template)
        self.assertIn("futures_tab_live_performance.html", template)
        self.assertIn("futures_tab_settings.html", template)

        self.assertIn("/api/futures-signals/performance/mock", script)
        self.assertIn("/api/futures-signals/performance/live", script)
        self.assertIn("/api/futures-signals/executor/state", script)


if __name__ == "__main__":
    unittest.main()
