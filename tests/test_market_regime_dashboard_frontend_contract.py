import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "static" / "css" / "style.css").read_text(encoding="utf-8")


class MarketRegimeDashboardFrontendContractTests(unittest.TestCase):
    def test_dashboard_exposes_daily_market_regime_panel(self):
        required_markup = (
            'data-dashboard-tab="market-regime"',
            'id="dashboard-tab-market-regime"',
            'id="market-regime-summary"',
            'id="table-market-regime-indices"',
            'id="market-regime-breadth"',
            'id="market-regime-reasons"',
            'id="market-regime-warnings"',
            'id="market-regime-checklist"',
            'id="table-market-regime-history"',
        )
        for marker in required_markup:
            with self.subTest(marker=marker):
                self.assertIn(marker, TEMPLATE)

    def test_frontend_reads_all_market_regime_contracts(self):
        self.assertIn("fetchJson('/api/market-regime/current'", APP_JS)
        self.assertIn("fetchJson('/api/market-regime/history?days=30'", APP_JS)
        self.assertIn("fetchJson('/api/market-regime/diagnostics'", APP_JS)
        self.assertIn("postJson('/api/market-regime/refresh'", APP_JS)
        self.assertIn("Promise.all([", APP_JS)

    def test_refresh_is_explicit_and_reports_busy_and_error_states(self):
        self.assertIn('id="btn-refresh-market-regime"', TEMPLATE)
        self.assertIn('id="market-regime-refresh-status"', TEMPLATE)
        self.assertIn('id="market-regime-error"', TEMPLATE)
        self.assertIn("async function refreshMarketRegimeData()", APP_JS)
        self.assertIn("setButtonBusy(button, true)", APP_JS)
        self.assertIn("데이터 다시 수집 실패", APP_JS)
        self.assertIn("addEventListener('click', refreshMarketRegimeData)", APP_JS)

    def test_market_regime_styles_include_quality_and_mobile_states(self):
        self.assertIn('.market-regime-hero[data-quality="good"]', STYLE)
        self.assertIn('.market-regime-hero[data-quality="degraded"]', STYLE)
        self.assertIn('.market-regime-hero[data-quality="insufficient"]', STYLE)
        self.assertIn(".market-regime-checklist", STYLE)
        self.assertIn("@media (max-width: 760px)", STYLE)


if __name__ == "__main__":
    unittest.main()
