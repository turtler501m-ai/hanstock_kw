import unittest
from unittest.mock import patch

from src.dashboard.routes import plunge_bounce


class PlungeBounceDashboardTests(unittest.TestCase):
    def test_scheduler_result_summary_removes_large_scan_payloads(self):
        result = {
            "plan": [{"symbol": str(i)} for i in range(60)],
            "results": [{"ok": True} for _ in range(60)],
            "auto_approved": [{"id": i} for i in range(60)],
            "errors": [{"message": str(i)} for i in range(20)],
            "auto_approval_errors": [{"message": str(i)} for i in range(20)],
            "candidate_scan": {
                "scanned": 176,
                "candidates_count": 3,
                "candidates": [{"symbol": str(i)} for i in range(30)],
                "scan_summary": [{"symbol": str(i), "payload": "x" * 1000} for i in range(176)],
            },
            "candidate_plan_rows": [{"large": True}],
            "position_plan_rows": [{"large": True}],
            "strategy_id": "plunge_bounce_strategy",
        }

        summary = plunge_bounce._summarize_scheduler_result(result)

        self.assertEqual(len(summary["plan"]), 50)
        self.assertEqual(len(summary["results"]), 50)
        self.assertEqual(len(summary["auto_approved"]), 50)
        self.assertEqual(len(summary["errors"]), 10)
        self.assertEqual(len(summary["auto_approval_errors"]), 10)
        self.assertEqual(summary["candidate_scan"]["scanned"], 176)
        self.assertEqual(summary["candidate_scan"]["candidates_count"], 3)
        self.assertEqual(len(summary["candidate_scan"]["candidates"]), 20)
        self.assertEqual(summary["candidate_scan"]["summary_count"], 176)
        self.assertNotIn("scan_summary", summary["candidate_scan"])
        self.assertNotIn("candidate_plan_rows", summary)
        self.assertNotIn("position_plan_rows", summary)

    def test_strategy_scan_requires_dedicated_universe(self):
        class FakeAPI:
            def get_balance(self):
                return {"output1": []}

        with patch("src.trader.KIStockAPI", return_value=FakeAPI()), \
                patch("src.db.repository.load_strategy_universe_symbols", return_value=[]), \
                patch("src.strategy.seven_split.find_candidates") as find_candidates:
            result = plunge_bounce._strategy_scan("heikin_ashi_scalping_strategy")

        find_candidates.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned_count"], 0)
        self.assertIn("dedicated universe", result["scan_error"])

    def test_plunge_bounce_scan_requires_dedicated_universe(self):
        class FakeAPI:
            def get_balance(self):
                return {"output1": []}

        with patch("src.trader.KIStockAPI", return_value=FakeAPI()), \
                patch("src.db.repository.load_strategy_universe_symbols", return_value=[]), \
                patch("src.strategy.seven_split.find_candidates") as find_candidates:
            result = plunge_bounce.run_plunge_bounce_scan()

        find_candidates.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned_count"], 0)
        self.assertIn("dedicated universe", result["scan_error"])


if __name__ == "__main__":
    unittest.main()
