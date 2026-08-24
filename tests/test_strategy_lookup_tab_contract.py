import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StrategyLookupTabContractTests(unittest.TestCase):
    def test_strategy_tab_is_named_for_lookup(self):
        html = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")
        self.assertIn('data-dashboard-tab="strategy">전략조회</button>', html)

    def test_lookup_moves_signals_and_removes_obsolete_cards(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("if (aiTab && signals) aiTab.insertBefore(signals, aiTab.firstChild);", script)
        self.assertIn("strategyTab?.querySelector('.panel-candidates-history')?.remove();", script)
        self.assertIn("strategyTab?.querySelector('.panel-execution-plan')?.remove();", script)

    def test_selected_strategies_run_analysis_only_before_candidate_render(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("async function previewSelectedStrategies()", script)
        self.assertIn("mode: 'analysis_only'", script)
        self.assertIn("auto_approve: false", script)
        self.assertIn("allowed_categories: ['candidate']", script)
        self.assertIn("await renderCachedStrategyPreviews(strategyIds, selected);", script)
        self.assertIn("await renderCandidates({ strategyIds, strategies: selected });", script)

    def test_opening_lookup_tab_only_reads_cached_strategy_results(self):
        app_script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        common_script = (ROOT / "web/static/js/common-analysis.js").read_text(encoding="utf-8")

        self.assertIn("async function renderStrategyLookupTab()", app_script)
        self.assertIn(
            "await renderCachedStrategyPreviews(strategyIds, selected, { updating: false });",
            app_script,
        )
        self.assertIn(
            "return refresh('strategy-lookup', renderStrategyLookupTab, 30000);",
            common_script,
        )
        strategy_branch = common_script.split("if (target === 'strategy') {", 1)[1].split("}", 1)[0]
        self.assertNotIn("refreshCommonAnalysisViews", strategy_branch)

    def test_cached_results_are_shown_while_selected_strategies_update(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        self.assertIn(
            "async function renderCachedStrategyPreviews(strategyIds, strategies = [], options = {})",
            script,
        )
        self.assertIn("cache_only: 'true'", script)
        self.assertIn("업데이트 중", script)
        self.assertIn("이전 결과", script)

    def test_refresh_is_always_available_and_runs_in_background(self):
        html = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

        self.assertIn('id="btn-refresh-strategy-lookup" class="button-ghost">새로고침</button>', html)
        self.assertIn("async function refreshStrategyLookup()", script)
        self.assertIn("await renderCachedStrategyPreviews(strategyIds, selected);", script)
        self.assertIn("await waitForStrategyPreviewCompletion(started.run_id);", script)
        self.assertIn("requested_run_matches", script)
        self.assertIn("finishStrategyPreviewUpdatingState();", script)
        self.assertIn(
            "await renderCachedStrategyPreviews(strategyIds, selected, { updating: false });",
            script,
        )
        self.assertIn("await renderCandidates({ strategyIds, strategies: selected });", script)
        self.assertNotIn(
            "await renderCandidates({ strategyIds, strategies: selected, refresh: true });",
            script,
        )
        self.assertIn("refresh: String(Boolean(options.refresh))", script)
        self.assertNotIn("refreshButton.hidden = false", script)
        self.assertIn("btnRefreshStrategyLookup.addEventListener('click', refreshStrategyLookup)", script)
        self.assertIn("DB 최신본 저장", script)
        self.assertIn("최대 10분까지 기다립니다", script)

    def test_lookup_completion_does_not_open_no_candidates_popup(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

        self.assertIn(
            "if (!previewStrategyIds.length) setNoCandidatesModalOpen(true);",
            script,
        )

    def test_each_selected_strategy_has_its_own_result_card(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "web/static/css/style.css").read_text(encoding="utf-8")
        self.assertIn("function renderStrategyPreviewCards(results, strategies = [])", script)
        self.assertIn("results.id = 'strategy-preview-results';", script)
        self.assertIn('class="strategy-preview-card"', script)
        self.assertIn(".strategy-preview-card", stylesheet)

    def test_each_strategy_card_lists_exclusions_and_check_items(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "web/static/css/style.css").read_text(encoding="utf-8")

        self.assertIn("function strategyAnalysisChecks(row)", script)
        self.assertIn("function strategyAnalysisChecklistMarkup(row)", script)
        self.assertIn("function strategyExcludedRowsMarkup(rows)", script)
        self.assertIn('const analyzedRows = data.scan_summary || [];', script)
        self.assertIn('class="strategy-analysis-details"', script)
        self.assertIn("분석 세부내역 · 통과", script)
        self.assertIn("Alpha HA 진입 형태", script)
        self.assertIn("RSI 과매도", script)
        self.assertIn("직전 고가 돌파", script)
        self.assertIn("손절 위험 허용", script)
        self.assertIn(".strategy-analysis-checklist", stylesheet)
        self.assertIn("max-height: 620px", stylesheet)

    def test_completion_badge_does_not_depend_on_previous_text(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        body = script.split("function finishStrategyPreviewUpdatingState() {", 1)[1].split(
            "\n}", 1
        )[0]

        self.assertIn("status.textContent = '업데이트 완료';", body)
        self.assertNotIn("status.textContent.includes", body)

    def test_lookup_runs_are_accumulated_and_open_inline(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

        self.assertIn("async function renderStrategyLookupHistory()", script)
        self.assertIn("/api/strategy-lookup/runs?limit=50", script)
        self.assertIn("조회할 때마다 결과가 누적됩니다.", script)
        self.assertIn("async function openStrategyLookupRun(runId)", script)
        self.assertIn("renderStrategyPreviewCards(results, aiStrategyCatalog)", script)
        self.assertGreaterEqual(script.count("await renderStrategyLookupHistory();"), 3)

    def test_analysis_details_support_score_and_sorting(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

        self.assertIn("function strategyAnalysisEvaluation(row)", script)
        self.assertIn("checklistScore", script)
        self.assertIn("100점이면서 기존 전략 점수 기준까지 통과", script)
        self.assertIn('class="strategy-analysis-sort"', script)
        self.assertIn("매매 가능 우선", script)

    def test_approved_independent_strategy_can_be_selected(self):
        script = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
        function_body = script.split(
            "function isSharedScheduleSelectable(strategy) {", 1
        )[1].split("}", 1)[0]
        self.assertIn("strategy.status", function_body)
        self.assertNotIn("independent_schedule", function_body)


if __name__ == "__main__":
    unittest.main()
