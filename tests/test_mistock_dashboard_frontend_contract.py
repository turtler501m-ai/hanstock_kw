import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from src.dashboard.routes import mistock


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "web" / "templates" / "mistock" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "static" / "js" / "mistock_app.js").read_text(encoding="utf-8")


class MistockDashboardFrontendContractTests(unittest.TestCase):
    def test_operations_route_is_registered(self):
        routes = {
            (method, route.path)
            for route in mistock.router.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("GET", "/api/mistock/operations"), routes)

    def test_operations_response_has_stable_dashboard_schema(self):
        # The operations endpoint is a read model.  Isolate it from broker, market-data,
        # and repository state so this contract test can never make a network call.
        pipeline = {"summary": {"decision_count": 1}, "records": {"decisions": []}}
        repository_loaders = (
            "list_strategy_positions",
            "list_strategy_decisions",
            "list_managed_orders",
            "list_risk_reservations",
            "list_position_protections",
            "list_unprotected_strategy_positions",
            "list_policies",
            "list_execution_runs",
        )
        with ExitStack() as stack:
            build_pipeline = stack.enter_context(
                patch("src.ai_stock.decision_pipeline_service.build_pipeline", return_value=pipeline)
            )
            for loader in repository_loaders:
                stack.enter_context(
                    patch(f"src.db.ai_dashboard_repository.{loader}", return_value=[])
                )
            payload = mistock.mistock_operations(strategy_id="us-alpha", limit=10)

        self.assertTrue(
            {
                "ok", "partial", "generated_at", "summary", "decision_flow",
                "managed_orders", "diagnostics", "sources", "source", "scope",
                "sections", "errors", "meta",
            }.issubset(payload)
        )
        self.assertEqual(payload["decision_flow"], pipeline)
        self.assertEqual(payload["scope"]["market"], "US")
        self.assertEqual(payload["scope"]["strategy_id"], "us-alpha")
        self.assertTrue(payload["scope"]["read_only"])
        self.assertEqual(payload["source"], "shared_ai_stock")
        self.assertEqual(payload["meta"]["data_domain"], "ai_stock_operational_evidence")
        build_pipeline.assert_called_once_with(market="US", strategy_id="us-alpha", limit=10)
        self.assertGreaterEqual(len(payload["sources"]), 1)
        for source in payload["sources"]:
            self.assertTrue({"key", "label", "as_of"}.issubset(source))
            self.assertTrue(source["label"])

    def test_operations_room_dom_contract(self):
        for fragment in (
            'id="dashboard-tab-operations"',
            'id="operations-summary-grid"',
            'id="operations-decisions"',
            'id="table-operations-orders"',
            'id="operations-diagnostics"',
            'id="operations-reservations"',
            'id="operations-protections"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML)

    def test_operations_javascript_fetches_and_renders_read_model(self):
        self.assertIn("/api/mistock/operations", APP_JS)
        self.assertIn("async function renderMistockOperations", APP_JS)
        for field in ("decision_flow", "managed_orders", "diagnostics", "sources"):
            with self.subTest(field=field):
                self.assertIn(field, APP_JS)

    def test_operations_ui_labels_every_external_data_source(self):
        self.assertIn("조회 전용 · 공통 AI 저장소", INDEX_HTML)
        self.assertIn("한스톡 공통 AI 파이프라인", INDEX_HTML)
        self.assertIn("US 시장 데이터", INDEX_HTML)
        self.assertIn("source.label", APP_JS)
        self.assertIn("source.as_of", APP_JS)
        self.assertIn("response.generated_at", APP_JS)

    def test_account_market_insights_tab_exposes_sources_and_as_of(self):
        for fragment in (
            'data-dashboard-tab="insights"',
            'id="dashboard-tab-insights"',
            'id="insights-pnl-breakdown"',
            'id="table-insights-reconciliation"',
            'id="insights-market-context"',
            'id="insights-scan-funnel"',
            'id="table-insights-protection"',
            'id="insights-sources"',
            'id="insights-updated-at"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML)
        self.assertIn("/api/mistock/insights", APP_JS)
        self.assertIn("async function renderMistockInsights", APP_JS)
        self.assertIn("renderInsightSources(data.sources)", APP_JS)
        self.assertIn("source.label", APP_JS)
        self.assertIn("source.as_of", APP_JS)

    def test_strategy_policy_and_schedule_editors_match_patch_endpoints(self):
        for fragment in (
            'id="mistock-strategy-detail-panel"',
            'id="form-edit-mistock-strategy"',
            'name="expected_version"',
            'id="form-watchlist-policy"',
            'id="watchlist-policy-stats"',
            'id="table-mistock-schedules"',
            'id="schedules-status"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML)
        self.assertIn("/api/mistock/ai-strategies/${encodeURIComponent", APP_JS)
        self.assertIn("/api/mistock/watchlist/policy", APP_JS)
        self.assertIn("fetchJson('/api/mistock/schedules')", APP_JS)
        self.assertIn("patchJson('/api/mistock/schedules'", APP_JS)
        self.assertIn("strategy_id:row.dataset.id", APP_JS)
        self.assertIn("interval_minutes", APP_JS)
        self.assertIn("start_hm", APP_JS)
        self.assertIn("end_hm", APP_JS)
        self.assertIn("row.last_errors", APP_JS)
        self.assertIn("row.last_status === 'failed'", APP_JS)
        self.assertIn("상태 및 오류", INDEX_HTML)
        self.assertNotIn("`/api/mistock/schedules/${", APP_JS)

        strategy_start = APP_JS.index("const strategyDetailForm =")
        strategy_end = APP_JS.index("const policyForm =", strategy_start)
        strategy_body = APP_JS[strategy_start:strategy_end]
        self.assertIn("profile:", strategy_body)
        self.assertIn("expected_version", strategy_body)

        policy_start = strategy_end
        policy_end = APP_JS.index("btn-refresh-schedules", policy_start)
        policy_body = APP_JS[policy_start:policy_end]
        self.assertNotIn("expected_version", policy_body)

    def test_managed_orders_ui_distinguishes_accepted_from_filled(self):
        for fragment in (
            'id="managed-orders-summary"',
            'id="managed-orders-source"',
            'id="managed-orders-as-of"',
            'id="managed-orders-sync"',
            'id="managed-order-status-filter"',
            'id="table-managed-orders"',
            'value="accepted"',
            'value="filled"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML)
        self.assertIn("/api/mistock/managed-orders", APP_JS)
        self.assertIn("/api/mistock/orders/cancel", APP_JS)
        self.assertIn("/api/mistock/orders/revise", APP_JS)
        self.assertIn("accepted:", APP_JS)
        self.assertIn("filled:", APP_JS)
        self.assertIn("['filled','demo_local_filled']", APP_JS)
        self.assertIn("data.summary", APP_JS)
        self.assertIn("접수는 체결이 아닙니다", INDEX_HTML + APP_JS)

    def test_diagnostics_and_batch_approval_frontend_contract(self):
        for fragment in (
            'id="btn-refresh-mistock-diagnostics"',
            'id="mistock-diagnostics-source"',
            'id="mistock-diagnostics-as-of"',
            'id="mistock-diagnostics-runtime"',
            'id="mistock-diagnostics-gates"',
            'id="mistock-diagnostics-orders"',
            'id="mistock-diagnostics-broker"',
            'id="select-all-approvals"',
            'id="btn-approve-selected"',
            'id="btn-reject-selected"',
            'id="approval-batch-result"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML)
        self.assertIn("/api/mistock/diagnostics", APP_JS)
        self.assertIn("data.runtime_flags", APP_JS)
        self.assertIn("data.generated_at", APP_JS)
        self.assertIn('class="approval-row-select"', APP_JS)
        self.assertIn("/api/mistock/approvals/batch", APP_JS)
        self.assertIn("{ids,action}", APP_JS)
        self.assertIn("confirm(`", APP_JS)
        batch_start = APP_JS.index("async function processApprovalBatch")
        batch_end = APP_JS.index("let pendingApprovalButton", batch_start)
        batch_body = APP_JS[batch_start:batch_end]
        self.assertIn("setButtonBusy(approve,true)", batch_body)
        self.assertIn("setButtonBusy(reject,true)", batch_body)
        self.assertIn("setButtonBusy(approve,false)", batch_body)
        self.assertIn("setButtonBusy(reject,false)", batch_body)

    def test_market_regime_and_multi_currency_pnl_frontend_contract(self):
        for fragment in (
            'id="market-regime-summary"',
            'id="table-market-regime"',
            "QQQ", "SPY", "SOXX", "VIX", "USDKRW",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML + APP_JS)
        self.assertIn("/api/mistock/market-regime", APP_JS)
        self.assertIn("data.assets", APP_JS)
        self.assertIn("data.market_session", APP_JS)
        self.assertIn("risk_multiplier", APP_JS)
        self.assertIn("row.as_of", APP_JS)
        for field in (
            "realized_net_usd", "realized_krw", "realized_net_krw",
            "fees_usd", "fees_krw", "tax_usd", "tax_krw", "fx_effect_krw",
        ):
            with self.subTest(field=field):
                self.assertIn(field, APP_JS)

    def test_strategy_workbench_frontend_contract(self):
        for fragment in (
            'id="mistock-strategy-workbench"',
            'id="strategy-workbench-summary"',
            'id="strategy-workbench-decisions"',
            'id="strategy-workbench-orders"',
            'id="table-strategy-workbench-events"',
            'id="btn-workbench-analysis"',
            'id="btn-workbench-execute"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, INDEX_HTML)
        self.assertIn("/api/mistock/strategy-workbench/${encodeURIComponent", APP_JS)
        self.assertIn("runStrategyWorkbench('analysis_only')", APP_JS)
        self.assertIn("runStrategyWorkbench('execute')", APP_JS)
        self.assertIn("sections.managed_orders", APP_JS)


if __name__ == "__main__":
    unittest.main()
