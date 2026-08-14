import unittest

from src.execution_plan import (
    build_execution_plan,
    candidate_order_to_plan_row,
    signal_to_plan_row,
)


class ExecutionPlanTests(unittest.TestCase):
    def test_signal_to_plan_row_skips_hold_by_default(self):
        row = signal_to_plan_row(
            "005930",
            "Samsung",
            {"action": "hold", "qty": 0, "price": 0, "reason": "hold", "indicators": {"rsi": 55}},
        )
        self.assertIsNone(row)

    def test_signal_to_plan_row_preserves_position_fields(self):
        row = signal_to_plan_row(
            "005930",
            "Samsung",
            {
                "action": "sell",
                "qty": 3,
                "price": 70000,
                "reason": "take profit",
                "indicators": {"rsi": 72, "macd_hist": -1.2},
            },
            metadata={"return_pct": 12.5},
        )

        self.assertEqual(row["symbol"], "005930")
        self.assertEqual(row["name"], "Samsung")
        self.assertEqual(row["category"], "position")
        self.assertEqual(row["action"], "sell")
        self.assertEqual(row["qty"], 3)
        self.assertEqual(row["price"], 70000)
        self.assertEqual(row["indicators"]["rsi"], 72)
        self.assertEqual(row["metadata"]["return_pct"], 12.5)

    def test_signal_to_plan_row_can_include_hold_rows(self):
        row = signal_to_plan_row(
            "005930",
            "Samsung",
            {"action": "hold", "qty": 0, "price": 0, "reason": "hold", "indicators": {}},
            include_hold=True,
        )

        self.assertEqual(row["action"], "hold")
        self.assertEqual(row["category"], "position")

    def test_candidate_order_to_plan_row_merges_candidate_and_order(self):
        row = candidate_order_to_plan_row(
            {
                "ticker": "000660",
                "name": "SK Hynix",
                "score": 4,
                "reasons": ["rsi", "macd"],
            },
            {
                "ticker": "000660",
                "quantity": 2,
                "limit_price": 120000,
                "estimated_cost": 240240.0,
                "score": 5,
                "reasons": ["rsi", "breakout"],
            },
            metadata={"scan_rank": 1},
        )

        self.assertEqual(row["symbol"], "000660")
        self.assertEqual(row["name"], "SK Hynix")
        self.assertEqual(row["category"], "candidate")
        self.assertEqual(row["action"], "buy")
        self.assertEqual(row["qty"], 2)
        self.assertEqual(row["price"], 120000)
        self.assertEqual(row["score"], 5)
        self.assertEqual(row["reasons"], ["rsi", "breakout"])
        self.assertIn("new buy score=5", row["reason"])
        self.assertEqual(row["metadata"]["scan_rank"], 1)

    def test_build_execution_plan_filters_none_rows(self):
        position_row = signal_to_plan_row(
            "005930",
            "Samsung",
            {"action": "sell", "qty": 1, "price": 70000, "reason": "trim", "indicators": {}},
        )
        candidate_row = candidate_order_to_plan_row(
            {"ticker": "000660", "score": 3, "reasons": ["rsi"]},
            {"ticker": "000660", "quantity": 1, "limit_price": 120000, "estimated_cost": 120120.0},
        )

        plan = build_execution_plan(position_rows=[position_row, None], candidate_rows=[candidate_row])

        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0]["category"], "position")
        self.assertEqual(plan[1]["category"], "candidate")


if __name__ == "__main__":
    unittest.main()
