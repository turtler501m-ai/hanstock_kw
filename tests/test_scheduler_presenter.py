import unittest

from src.dashboard.presenters.scheduler_presenter import (
    _compact_scheduler_status_result,
)


class SchedulerPresenterTests(unittest.TestCase):
    def test_multi_strategy_result_keeps_run_and_block_reasons(self):
        payload = {
            "result": {
                "status": "success",
                "ok": True,
                "strategy_ids": ["s1"],
                "runs": [
                    {
                        "strategy_id": "s1",
                        "cycle_id": "c1",
                        "result": {
                            "scan": {"candidate_count": 17, "status": "completed"},
                            "automation": {
                                "planned": 0,
                                "blocked": ["invalid candidate price"],
                            },
                            "autonomy": {"error": "invalid candidate price"},
                            "market_regime_policy": {
                                "regime": "sideways_low_vol",
                                "allowed": False,
                                "multiplier": 0.0,
                                "reason": "market_regime_not_allowed",
                            },
                        },
                    }
                ],
                "errors": [],
            }
        }

        compact = _compact_scheduler_status_result(payload)

        self.assertEqual(compact["result"]["summary_counts"]["run_count"], 1)
        self.assertEqual(compact["result"]["summary_counts"]["blocked_count"], 1)
        self.assertEqual(
            compact["result"]["runs"][0]["blocked"],
            ["invalid candidate price"],
        )
        self.assertEqual(
            compact["result"]["runs"][0]["market_regime_policy"]["regime"],
            "sideways_low_vol",
        )

    def test_single_strategy_compaction_keeps_regime_policy_and_blocks(self):
        payload = {"result": {
            "status": "blocked",
            "ok": True,
            "results": [],
            "market_regime_policy": {
                "regime": "bear",
                "allowed": False,
                "multiplier": 0.0,
            },
            "blocked": ["market_regime:market_regime_zero_risk"],
        }}
        compact = _compact_scheduler_status_result(payload)
        self.assertEqual(compact["result"]["market_regime_policy"]["regime"], "bear")
        self.assertEqual(len(compact["result"]["blocked"]), 1)

    def test_compaction_counts_scheduler_run_outcomes_separately_from_orders(self):
        payload = {"result": {
            "status": "blocked",
            "execution_status": "blocked",
            "results": [{"decision": "skip"}],
            "execution_runs": [
                {"status": "success"},
                {"status": "blocked", "message": "market regime not allowed"},
                {"status": "failed", "message": "quote unavailable"},
            ],
        }}

        compact = _compact_scheduler_status_result(payload)
        counts = compact["result"]["summary_counts"]

        self.assertEqual(compact["result"]["execution_status"], "blocked")
        self.assertEqual(counts["run_count"], 3)
        self.assertEqual(counts["run_success_count"], 1)
        self.assertEqual(counts["run_blocked_count"], 1)
        self.assertEqual(counts["run_failed_count"], 1)

    def test_compaction_keeps_holding_quantity_and_current_price(self):
        payload = {"result": {"results": [{
            "symbol": "005930",
            "action": "hold",
            "qty": 0,
            "price": 0,
            "holding_qty": 7,
            "current_price": 71000,
        }]}}

        compact = _compact_scheduler_status_result(payload)

        row = compact["result"]["results"][0]
        self.assertEqual(row["holding_qty"], 7)
        self.assertEqual(row["current_price"], 71000)

    def test_compaction_counts_policy_rejection_separately_from_failure(self):
        payload = {"result": {
            "auto_approved": [
                {"id": 1, "status": "rejected", "response_msg": "risk policy"},
                {"id": 2, "status": "failed", "response_msg": "broker failure"},
            ],
        }}

        compact = _compact_scheduler_status_result(payload)
        counts = compact["result"]["summary_counts"]

        self.assertEqual(counts["rejected_count"], 1)
        self.assertEqual(counts["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
