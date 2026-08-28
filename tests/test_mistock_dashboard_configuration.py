import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard.routes import mistock
from src.mistock import db as mistock_db
from src.mistock.config import config as mistock_config


class MistockDashboardConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = mistock_config.trade_db_path
        object.__setattr__(
            mistock_config,
            "trade_db_path",
            Path(self.temp_dir.name) / "mistock-config.sqlite",
        )
        mistock_db.init_db()

    def tearDown(self) -> None:
        object.__setattr__(mistock_config, "trade_db_path", self.original_db_path)
        self.temp_dir.cleanup()

    def test_configuration_routes_are_registered_with_patch_methods(self):
        routes = {
            (method, route.path)
            for route in mistock.router.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("PATCH", "/api/mistock/ai-strategies/{strategy_id}"), routes)
        self.assertIn(("GET", "/api/mistock/watchlist/policy"), routes)
        self.assertIn(("PATCH", "/api/mistock/watchlist/policy"), routes)
        self.assertIn(("GET", "/api/mistock/schedules"), routes)
        self.assertIn(("PATCH", "/api/mistock/schedules"), routes)

    def test_strategy_patch_uses_optimistic_version_and_records_event(self):
        strategy_id = "mistock_nasdaq_rule_v1"
        before = mistock_db.row("SELECT * FROM ai_strategies WHERE id=?", (strategy_id,))
        version = int(before["strategy_version"])

        result = mistock.mistock_patch_ai_strategy(strategy_id, {
            "expected_version": version,
            "name": "US Momentum Reviewed",
            "description": "reviewed in dashboard",
            "weight": 0.35,
            "profile": {"market": "NASDAQ", "min_score": 5},
        })

        strategy = result["strategy"]
        self.assertTrue(result["review_required"])
        self.assertEqual(strategy["strategy_version"], version + 1)
        self.assertEqual(strategy["status"], "review_required")
        self.assertEqual(strategy["weight"], 0.35)
        self.assertEqual(json.loads(strategy["profile_json"])["min_score"], 5)
        self.assertEqual(len(strategy["profile_hash"]), 64)
        event = mistock_db.row(
            "SELECT * FROM ai_strategy_events WHERE strategy_id=? ORDER BY id DESC LIMIT 1",
            (strategy_id,),
        )
        self.assertEqual(event["event_type"], "strategy_edited")
        self.assertEqual(event["strategy_version"], version + 1)
        self.assertIn("profile", json.loads(event["payload"])["changed_fields"])

        with self.assertRaises(mistock.HTTPException) as conflict:
            mistock.mistock_patch_ai_strategy(strategy_id, {
                "expected_version": version,
                "description": "stale write",
            })
        self.assertEqual(conflict.exception.status_code, 409)

    def test_strategy_patch_rejects_fields_outside_allowlist(self):
        for payload in (
            {"provider": "openai"},
            {"selected": True},
            {"status": "approved"},
            {"weight": 1.01},
            {"profile": []},
        ):
            with self.subTest(payload=payload), self.assertRaises(mistock.HTTPException) as raised:
                mistock.mistock_patch_ai_strategy("mistock_nasdaq_rule_v1", payload)
            self.assertEqual(raised.exception.status_code, 400)

    def test_watchlist_policy_patch_validates_persists_and_summarizes(self):
        result = mistock.mistock_patch_watchlist_policy({
            "enabled": True,
            "max_symbols": 2,
            "allow_auto_add": True,
            "min_score": 4.5,
            "block_held": True,
            "block_pending": True,
            "rebuy_cooldown_hours": 48,
        })
        loaded = mistock.mistock_get_watchlist_policy()

        self.assertTrue(result["ok"])
        self.assertEqual(loaded["policy"]["max_symbols"], 2)
        self.assertEqual(loaded["policy"]["min_score"], 4.5)
        self.assertTrue(loaded["policy"]["allow_auto_add"])
        self.assertTrue(loaded["read_only_evaluation"])
        self.assertEqual(mistock_db.get_setting("ai_auto_add"), "true")
        self.assertEqual(mistock_db.get_setting("ai_auto_add_threshold"), "4.5")
        self.assertTrue({"allowed", "blocked", "allowed_count", "blocked_count"}.issubset(loaded["summary"]))

        summary = mistock._mistock_watchlist_policy_summary(
            loaded["policy"],
            [
                {"symbol": "AAPL", "score": 6},
                {"symbol": "MSFT", "score": 6},
                {"symbol": "NVDA", "score": 6},
            ],
            {"AAPL"},
            {"MSFT"},
            set(),
        )
        blocked = {row["symbol"]: row["blocked_reasons"] for row in summary["blocked"]}
        self.assertIn("held", blocked["AAPL"])
        self.assertIn("pending_order", blocked["MSFT"])
        self.assertEqual(summary["allowed_count"], 1)

    def test_watchlist_policy_rejects_invalid_values(self):
        for payload in (
            {"enabled": "yes"},
            {"max_symbols": 0},
            {"min_score": 101},
            {"rebuy_cooldown_hours": -1},
            {"unknown": True},
        ):
            with self.subTest(payload=payload), self.assertRaises(mistock.HTTPException) as raised:
                mistock.mistock_patch_watchlist_policy(payload)
            self.assertEqual(raised.exception.status_code, 400)

    def test_schedule_patch_validates_and_persists(self):
        strategy_id = "mistock_nasdaq_rule_v1"
        result = mistock.mistock_patch_schedule({
            "strategy_id": strategy_id,
            "enabled": True,
            "interval_minutes": 30,
            "start_hm": "2130",
            "end_hm": "0530",
            "weekdays": "1-5",
            "mode": "analysis_only",
            "auto_approve": False,
        })
        loaded = mistock.mistock_schedules()
        row = next(item for item in loaded["schedules"] if item["strategy_id"] == strategy_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["schedule"], row)
        self.assertEqual(row["interval_minutes"], 30)
        self.assertEqual(row["start_hm"], "2130")
        self.assertEqual(row["mode"], "analysis_only")
        self.assertFalse(row["auto_approve"])
        self.assertEqual(loaded["count"], len(loaded["schedules"]))

    def test_schedule_list_includes_exact_latest_failure(self):
        result_path = Path(self.temp_dir.name) / "latest-result.json"
        result_path.write_text(json.dumps({
            "recorded_at": "2026-08-28T04:47:13+09:00",
            "result": {
                "strategy_id": "mistock_nasdaq_rule_v1",
                "status": "failed",
                "ok": False,
                "errors": [{
                    "symbol": "AVB",
                    "action": "buy",
                    "message": "키움 오류[1903:종목 정보가 없습니다]",
                }],
            },
        }, ensure_ascii=False), encoding="utf-8")

        with patch.dict("os.environ", {"MISTOCK_SCHEDULER_RESULT_PATH": str(result_path)}):
            loaded = mistock.mistock_schedules()

        row = next(item for item in loaded["schedules"] if item["strategy_id"] == "mistock_nasdaq_rule_v1")
        self.assertEqual(row["last_status"], "failed")
        self.assertFalse(row["last_ok"])
        self.assertEqual(row["last_result_at"], "2026-08-28T04:47:13+09:00")
        self.assertEqual(row["last_errors"][0]["message"], "키움 오류[1903:종목 정보가 없습니다]")

    def test_schedule_rejects_invalid_payloads(self):
        strategy_id = "mistock_nasdaq_rule_v1"
        invalid = (
            {},
            {"strategy_id": strategy_id, "enabled": "yes"},
            {"strategy_id": strategy_id, "interval_minutes": 0},
            {"strategy_id": strategy_id, "start_hm": "25:00"},
            {"strategy_id": strategy_id, "weekdays": "0-8"},
            {"strategy_id": strategy_id, "mode": "live"},
            {"strategy_id": strategy_id, "unknown": True},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(mistock.HTTPException) as raised:
                mistock.mistock_patch_schedule(payload)
            self.assertIn(raised.exception.status_code, {400, 404})


if __name__ == "__main__":
    unittest.main()
