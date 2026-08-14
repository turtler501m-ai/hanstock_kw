import asyncio
import unittest
import os
import tempfile
from unittest.mock import patch

import src.dashboard as dashboard


class FuturesSignalsDashboardTests(unittest.TestCase):
    @staticmethod
    def _find_route(path, method="GET"):
        for route in dashboard.app.routes:
            methods = getattr(route, "methods", set()) or set()
            if method in methods and getattr(route, "path", "") == path:
                return route
        raise AssertionError(f"Missing {method} route for {path}")

    def setUp(self):
        dashboard._FUTURES_SIGNAL_SERVICE = None
        self._old_signals_db_path = os.environ.get("SIGNALS_DB_PATH")
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["SIGNALS_DB_PATH"] = os.path.join(self._tmpdir.name, "signals.db")

    def tearDown(self):
        if self._old_signals_db_path is None:
            os.environ.pop("SIGNALS_DB_PATH", None)
        else:
            os.environ["SIGNALS_DB_PATH"] = self._old_signals_db_path
        self._tmpdir.cleanup()

    def test_dashboard_route_points_to_futures_template(self):
        route = self._find_route("/futures-signals")

        response = route.endpoint()

        self.assertEqual(str(response.path), str(dashboard.WEB_DIR / "templates" / "futures_signals.html"))

    def test_summary_route_returns_deterministic_sample_summary(self):
        route = self._find_route("/api/futures-signals/summary")

        summary = route.endpoint()

        self.assertEqual(summary["source"], "service")
        self.assertFalse(summary["telegram_connected"])
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["verified"], 1)
        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(summary["latest_signal_at"], "2026-05-05T10:40:00+09:00")

    def test_signals_route_returns_deterministic_sample_list(self):
        route = self._find_route("/api/futures-signals")

        payload = route.endpoint()

        signals = payload["signals"]
        by_id = {signal["id"]: signal for signal in signals}
        self.assertEqual(set(by_id), {"tg-sample-001", "tg-sample-002", "tg-sample-003"})
        self.assertEqual(by_id["tg-sample-001"]["symbol"], "MNQM26")
        self.assertEqual(by_id["tg-sample-001"]["status"], "verified")
        self.assertEqual(by_id["tg-sample-002"]["verification"]["requires_manual_review"], True)
        self.assertEqual(by_id["tg-sample-003"]["status"], "rejected")

    def test_parse_route_adds_manual_signal(self):
        route = self._find_route("/api/futures-signals/parse", method="POST")

        payload = route.endpoint({
            "text": "MES M26 LONG Entry: 5300 SL 5290 TP1 5320",
            "source": "telegram_manual",
            "source_message_id": "manual-1",
        })

        signal = payload["signal"]
        self.assertEqual(signal["id"], "manual-1")
        self.assertEqual(signal["symbol"], "MESM26")
        self.assertEqual(signal["status"], "parsed")

    def test_verify_route_updates_signal_result(self):
        parse_route = self._find_route("/api/futures-signals/parse", method="POST")
        verify_route = self._find_route("/api/futures-signals/{signal_id}/verify", method="POST")
        parse_route.endpoint({
            "text": "NQ M26 SHORT Entry: 18500 SL 18550 TP1 18420",
            "source_message_id": "verify-1",
        })

        payload = verify_route.endpoint("verify-1", {
            "candles": [
                {"timestamp": "2026-05-05T11:00:00+09:00", "open": 18500, "high": 18510, "low": 18410, "close": 18430}
            ]
        })

        self.assertEqual(payload["signal"]["status"], "verified")
        self.assertEqual(payload["signal"]["verification"]["hit_price"], 18420.0)

    def test_collector_status_route_is_safe_without_credentials(self):
        route = self._find_route("/api/futures-signals/collector/status")

        payload = route.endpoint()

        self.assertIn("ready", payload)
        self.assertIn("missing", payload)

    def test_collector_run_route_returns_not_ready_without_credentials(self):
        route = self._find_route("/api/futures-signals/collector/run", method="POST")

        with patch.object(dashboard, "collector_status", return_value={"ready": False, "missing": ["TELEGRAM_API_ID"]}):
            result = route.endpoint({})
            # async 엔드포인트인 경우 코루틴을 실행
            if asyncio.iscoroutine(result):
                payload = asyncio.run(result)
            else:
                payload = result

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["ingested"], 0)
