import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db.strategy_lookup_repository import (
    list_strategy_lookup_runs,
    load_strategy_lookup_run,
    save_strategy_lookup_result,
)


class StrategyLookupRepositoryTests(unittest.TestCase):
    def test_each_attempt_is_accumulated_and_grouped_by_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "lookup.db"
            with patch("src.db.repository.config.trade_db_path", str(db_path)):
                save_strategy_lookup_result(
                    "run-1",
                    "strategy-a",
                    {"scanned": 10, "min_score": 2, "candidates": [], "scan_summary": [{"ticker": "A"}]},
                    captured_at="2026-08-24T10:00:00+09:00",
                )
                save_strategy_lookup_result(
                    "run-1",
                    "strategy-b",
                    {"scanned": 20, "min_score": 2, "candidates": [{"ticker": "B"}], "scan_summary": []},
                    captured_at="2026-08-24T10:00:01+09:00",
                )
                save_strategy_lookup_result(
                    "run-2",
                    "strategy-a",
                    {"scanned": 30, "min_score": 2, "candidates": [], "scan_summary": []},
                    captured_at="2026-08-24T11:00:00+09:00",
                )

                runs = list_strategy_lookup_runs()
                details = load_strategy_lookup_run("run-1")

        self.assertEqual([item["run_id"] for item in runs], ["run-2", "run-1"])
        self.assertEqual(runs[1]["strategy_count"], 2)
        self.assertEqual(runs[1]["scanned"], 30)
        self.assertEqual(runs[1]["candidate_count"], 1)
        self.assertEqual(len(details), 2)
        self.assertEqual(details[0]["data"]["scan_summary"][0]["ticker"], "A")


if __name__ == "__main__":
    unittest.main()
