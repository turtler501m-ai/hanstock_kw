import unittest
from unittest.mock import MagicMock, patch

from src.dashboard.routes import mistock


class MistockDashboardDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_and_batch_routes_are_registered(self):
        routes = {
            (method, route.path)
            for route in mistock.router.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("GET", "/api/mistock/diagnostics"), routes)
        self.assertIn(("POST", "/api/mistock/approvals/batch"), routes)

    def test_diagnostics_reports_runtime_gates_without_network_calls(self):
        run_state = MagicMock()
        run_state.copy.return_value = {"is_running": False, "completed_at": "2026-08-21 09:00:00"}

        def rows(sql, _params=()):
            if "FROM approvals" in sql:
                return [{"status": "pending", "count": 2}]
            if "FROM managed_orders" in sql:
                return [{"status": "accepted", "count": 1}]
            if "FROM holdings" in sql:
                return []
            self.fail(f"unexpected SQL: {sql}")

        with patch.object(mistock, "_mistock_scheduler_run_state", run_state), patch.object(
            mistock, "load_mistock_daily_runs", return_value=[]
        ), patch.object(mistock, "merge_mistock_runs", return_value=None), patch.object(
            mistock.mistock_db, "rows", side_effect=rows
        ), patch.object(
            mistock.mistock_trader, "runtime_flags",
            return_value={"dry_run": True, "order_submission_enabled": False},
        ), patch.object(mistock.mistock_trader, "_overseas_balance_cache", None), patch(
            "src.mistock.scheduler.is_us_market_open", return_value=False
        ), patch(
            "src.online_access.is_online_access_blocked", return_value=False
        ), patch(
            "src.db.ai_dashboard_repository.list_strategy_positions", return_value=[]
        ), patch(
            "src.db.ai_dashboard_repository.list_unprotected_strategy_positions", return_value=[]
        ), patch.object(
            mistock.mistock_trader, "get_balance", side_effect=AssertionError("network balance call")
        ), patch.object(
            mistock.mistock_trader, "quote", side_effect=AssertionError("network quote call")
        ):
            payload = mistock.mistock_diagnostics()

        self.assertTrue(payload["scope"]["read_only"])
        self.assertFalse(payload["scope"]["network_calls"])
        self.assertEqual(payload["approvals"]["pending"], 2)
        self.assertEqual(payload["managed_orders"]["unsettled_count"], 1)
        self.assertFalse(payload["broker_cache"]["refreshed"])
        gate_codes = {gate["code"] for gate in payload["gates"]}
        self.assertTrue({
            "online_access", "market_open", "live_trading", "scheduler",
            "reconciliation", "position_protection", "managed_orders", "broker_cache",
        }.issubset(gate_codes))
        for gate in payload["gates"]:
            self.assertTrue({"code", "label", "ok", "reason", "severity"}.issubset(gate))

    def test_diagnostics_isolates_a_section_failure(self):
        run_state = MagicMock()
        run_state.copy.return_value = {"is_running": False}
        with patch.object(mistock, "_mistock_scheduler_run_state", run_state), patch.object(
            mistock, "load_mistock_daily_runs", side_effect=RuntimeError("runs unavailable")
        ), patch.object(mistock.mistock_db, "rows", return_value=[]), patch.object(
            mistock.mistock_trader, "runtime_flags", return_value={"dry_run": True}
        ), patch.object(mistock.mistock_trader, "_overseas_balance_cache", None), patch(
            "src.mistock.scheduler.is_us_market_open", return_value=False
        ), patch(
            "src.online_access.is_online_access_blocked", return_value=False
        ), patch(
            "src.db.ai_dashboard_repository.list_strategy_positions", return_value=[]
        ), patch(
            "src.db.ai_dashboard_repository.list_unprotected_strategy_positions", return_value=[]
        ):
            payload = mistock.mistock_diagnostics()

        self.assertTrue(payload["partial"])
        self.assertIn("scheduler", payload["errors"])
        self.assertEqual(payload["scheduler"]["heartbeat"], "unknown")
        self.assertIn("gates", payload)
        self.assertIn("managed_orders", payload)

    def test_batch_deduplicates_ids_and_uses_existing_atomic_path(self):
        def row(_sql, params=()):
            return {"status": "pending", "id": int(params[0])}

        def execute(approval_id, *, approve):
            return {"id": approval_id, "status": "executed" if approve else "rejected", "ok": True}

        with patch.object(mistock.mistock_db, "row", side_effect=row), patch.object(
            mistock, "_execute_approval", side_effect=execute
        ) as atomic:
            approved = mistock.mistock_approval_batch({"ids": [1, 1, "2"], "action": "approve"})
            rejected = mistock.mistock_approval_batch({"ids": [3], "action": "reject"})

        self.assertEqual(approved["summary"], {"requested": 2, "success": 2, "failed": 0, "skipped": 0})
        self.assertEqual(rejected["summary"]["success"], 1)
        self.assertEqual(
            [(call.args[0], call.kwargs["approve"]) for call in atomic.call_args_list],
            [(1, True), (2, True), (3, False)],
        )
        self.assertEqual(approved["execution"], {"mode": "synchronous", "bounded": True, "max_items": 50})

    def test_batch_isolates_one_failure_and_skips_non_pending(self):
        states = {1: "pending", 2: "pending", 3: "executed"}

        def row(_sql, params=()):
            return {"status": states[int(params[0])]}

        def execute(approval_id, *, approve):
            if approval_id == 2:
                raise mistock.HTTPException(status_code=409, detail="market closed")
            return {"id": approval_id, "status": "executed", "ok": True}

        with patch.object(mistock.mistock_db, "row", side_effect=row), patch.object(
            mistock, "_execute_approval", side_effect=execute
        ):
            payload = mistock.mistock_approval_batch({"ids": [1, 2, 3], "action": "approve"})

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["summary"], {"requested": 3, "success": 1, "failed": 1, "skipped": 1})
        outcomes = {row["id"]: row["outcome"] for row in payload["results"]}
        self.assertEqual(outcomes, {1: "success", 2: "failed", 3: "skipped"})

    def test_batch_validates_action_ids_and_unique_limit(self):
        invalid = (
            {"ids": [1], "action": "execute"},
            {"ids": [], "action": "approve"},
            {"ids": ["bad"], "action": "approve"},
            {"ids": [0], "action": "reject"},
            {"ids": list(range(1, 52)), "action": "approve"},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(mistock.HTTPException) as raised:
                mistock.mistock_approval_batch(payload)
            self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
