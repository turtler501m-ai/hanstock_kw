import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard.routes import mistock
from src.mistock import db as mistock_db
from src.mistock.config import config as mistock_config


class MistockStrategyWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = mistock_config.trade_db_path
        object.__setattr__(mistock_config, "trade_db_path", Path(self.temp_dir.name) / "workbench.sqlite")
        mistock_db.init_db()

    def tearDown(self):
        object.__setattr__(mistock_config, "trade_db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_routes_and_read_only_aggregate_contract(self):
        routes = {(method, route.path) for route in mistock.router.routes for method in route.methods}
        self.assertIn(("GET", "/api/mistock/strategy-workbench/{strategy_id}"), routes)
        self.assertIn(("POST", "/api/mistock/strategy-workbench/{strategy_id}/run"), routes)

        result = mistock.mistock_strategy_workbench("mistock_nasdaq_rule_v1")

        self.assertTrue(result["read_only"])
        self.assertEqual(result["strategy_id"], "mistock_nasdaq_rule_v1")
        for key in ("strategy", "schedule", "candidates", "approvals", "managed_orders", "trades", "events", "performance"):
            self.assertIn(key, result["sections"])
        self.assertEqual(result["source"], "mistock_persisted_strategy_workbench")
        self.assertIn("as_of", result)

    def test_run_delegates_to_existing_scheduler_policy(self):
        with patch.object(mistock, "mistock_scheduler_run", return_value={"accepted": True}) as scheduler:
            result = mistock.mistock_strategy_workbench_run(
                "mistock_nasdaq_rule_v1", {"mode": "analysis_only"}
            )

        self.assertTrue(result["delegated"])
        scheduler.assert_called_once_with({
            "mode": "analysis_only",
            "strategy_ids": ["mistock_nasdaq_rule_v1"],
        })

    def test_run_rejects_unknown_strategy_and_mode(self):
        with self.assertRaises(mistock.HTTPException) as missing:
            mistock.mistock_strategy_workbench_run("missing", {"mode": "analysis_only"})
        self.assertEqual(missing.exception.status_code, 404)

        with self.assertRaises(mistock.HTTPException) as invalid:
            mistock.mistock_strategy_workbench_run("mistock_nasdaq_rule_v1", {"mode": "unsafe"})
        self.assertEqual(invalid.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
