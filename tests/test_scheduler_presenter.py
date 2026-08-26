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


if __name__ == "__main__":
    unittest.main()
