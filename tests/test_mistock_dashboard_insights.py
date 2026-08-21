import unittest
from unittest.mock import patch

from src.dashboard.routes import mistock


class MistockDashboardInsightsTests(unittest.TestCase):
    def test_insights_route_is_registered(self):
        routes = {
            (method, route.path)
            for route in mistock.router.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("GET", "/api/mistock/insights"), routes)

    def test_insights_response_contract_uses_only_persisted_data(self):
        def local_rows(sql, _params=()):
            if "FROM trades" in sql:
                return [{
                    "symbol": "AAPL", "action": "buy", "qty": 2, "price": 100,
                    "ok": 1, "order_status": "filled", "fee": 1.25, "tax": 0,
                }]
            if "FROM holdings" in sql:
                return [{"symbol": "AAPL", "name": "Apple", "qty": 2, "avg_price": 100}]
            if "FROM scanned_candidates" in sql:
                return [{
                    "symbol": "NVDA", "name": "NVIDIA", "score": 6,
                    "scanned_at": "2026-08-21 09:00:00", "market_regime": "risk_on",
                }]
            self.fail(f"unexpected local query: {sql}")

        with patch.object(mistock.mistock_db, "rows", side_effect=local_rows), patch(
            "src.db.ai_dashboard_repository.list_strategy_positions",
            return_value=[{"symbol": "AAPL", "remaining_qty": 2, "status": "open"}],
        ), patch(
            "src.db.ai_dashboard_repository.list_candidates",
            return_value=[{
                "symbol": "QQQ", "market_regime": "risk_on",
                "data_source": "shared_ai_stock_repository",
                "data_as_of": "2026-08-21T00:00:00Z",
            }],
        ), patch(
            "src.db.ai_dashboard_repository.list_position_protections",
            return_value=[{"symbol": "AAPL", "status": "active"}],
        ), patch(
            "src.db.ai_dashboard_repository.list_unprotected_strategy_positions",
            return_value=[],
        ), patch.object(mistock.mistock_trader, "_overseas_balance_cache", None):
            payload = mistock.mistock_insights()

        expected = {
            "ok", "partial", "generated_at", "read_only", "source", "scope",
            "pnl_breakdown", "reconciliation", "scan_diagnostics",
            "position_protection", "market_context", "sources", "errors",
        }
        self.assertEqual(set(payload), expected)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["scope"]["network_calls"])
        self.assertEqual(payload["scope"]["market"], "US")
        self.assertEqual(payload["source"], "mistock_persisted_and_shared_ai_stock")
        self.assertGreaterEqual(len(payload["sources"]), 1)
        for source in payload["sources"]:
            self.assertTrue({"key", "label", "as_of"}.issubset(source))
            self.assertTrue(source["label"])
        self.assertEqual(payload["position_protection"]["protected_count"], 1)
        self.assertEqual(payload["market_context"]["regime"], "risk_on")

    def test_unavailable_calculations_use_null_and_availability_reason(self):
        pnl = mistock._mistock_insight_pnl([], None)
        scan = mistock._mistock_insight_scan_diagnostics([])
        market = mistock._mistock_insight_market_context([])

        self.assertIsNone(pnl["unrealized"]["value"])
        self.assertEqual(pnl["unrealized"]["availability"], "unavailable")
        self.assertTrue(pnl["unrealized"]["reason"])
        self.assertIsNone(pnl["fx_effect"]["value"])
        self.assertEqual(pnl["fx_effect"]["availability"], "unavailable")
        self.assertTrue(pnl["fx_effect"]["reason"])
        self.assertIsNone(scan["scanned"])
        self.assertEqual(scan["availability"], "unavailable")
        self.assertTrue(scan["reason"])
        self.assertIsNone(market["regime"])
        self.assertEqual(market["availability"], "unavailable")
        self.assertTrue(market["reason"])

    def test_reconciliation_calculates_quantity_deltas_and_status(self):
        result = mistock._mistock_insight_reconciliation(
            [
                {"symbol": "AAPL", "qty": 2},
                {"symbol": "MSFT", "qty": 1},
            ],
            [
                {"symbol": "AAPL", "qty": 2},
                {"symbol": "MSFT", "qty": 3},
            ],
            [
                {"symbol": "AAPL", "remaining_qty": 2, "status": "open"},
                {"symbol": "MSFT", "remaining_qty": 1, "status": "exit_pending"},
                {"symbol": "IGNORED", "remaining_qty": 9, "status": "closed"},
            ],
            as_of="2026-08-21T01:00:00Z",
        )
        rows = {row["symbol"]: row for row in result["rows"]}

        self.assertEqual(rows["AAPL"]["delta"], 0)
        self.assertEqual(rows["AAPL"]["strategy_delta"], 0)
        self.assertEqual(rows["AAPL"]["status"], "reconciled")
        self.assertEqual(rows["MSFT"]["delta"], 2)
        self.assertEqual(rows["MSFT"]["strategy_delta"], 0)
        self.assertEqual(rows["MSFT"]["status"], "mismatch")
        self.assertEqual(rows["MSFT"]["as_of"], "2026-08-21T01:00:00Z")
        self.assertEqual(result["mismatch_count"], 1)
        self.assertNotIn("IGNORED", rows)

    def test_section_failure_is_reported_without_hiding_other_sections(self):
        with patch.object(mistock.mistock_db, "rows", return_value=[]), patch(
            "src.db.ai_dashboard_repository.list_strategy_positions",
            side_effect=RuntimeError("position repository unavailable"),
        ), patch(
            "src.db.ai_dashboard_repository.list_candidates", return_value=[]
        ), patch(
            "src.db.ai_dashboard_repository.list_position_protections", return_value=[]
        ), patch(
            "src.db.ai_dashboard_repository.list_unprotected_strategy_positions", return_value=[]
        ), patch.object(mistock.mistock_trader, "_overseas_balance_cache", None):
            payload = mistock.mistock_insights()

        self.assertFalse(payload["ok"])
        self.assertTrue(payload["partial"])
        self.assertIn("_strategy_positions", payload["errors"])
        self.assertIn("pnl_breakdown", payload)
        self.assertIn("scan_diagnostics", payload)


if __name__ == "__main__":
    unittest.main()
