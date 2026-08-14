import unittest

from src.execution_service import (
    ExecutionContext,
    resolve_execution_decision,
    submit_order_request,
)
from src.trader import normalize_run_mode


class ExecutionPolicyTests(unittest.TestCase):
    def test_analysis_only_queues_orders(self):
        decision = resolve_execution_decision(
            ExecutionContext(
                dry_run=False,
                trading_env="demo",
                enable_live_trading=False,
                require_approval=False,
                analysis_only=True,
            )
        )
        self.assertEqual(decision.decision, "queue")

    def test_require_approval_queues_orders(self):
        decision = resolve_execution_decision(
            ExecutionContext(
                dry_run=False,
                trading_env="demo",
                enable_live_trading=False,
                require_approval=True,
            )
        )
        self.assertEqual(decision.decision, "queue")

    def test_real_env_without_live_switch_rejects(self):
        decision = resolve_execution_decision(
            ExecutionContext(
                dry_run=False,
                trading_env="real",
                enable_live_trading=False,
                require_approval=False,
            )
        )
        self.assertEqual(decision.decision, "reject")

    def test_manual_approval_bypasses_approval_queue(self):
        decision = resolve_execution_decision(
            ExecutionContext(
                dry_run=False,
                trading_env="demo",
                enable_live_trading=False,
                require_approval=True,
            ),
            allow_approval_bypass=True,
        )
        self.assertEqual(decision.decision, "execute")

    def test_submit_order_request_queues_when_policy_requires(self):
        queued = []
        saved = []

        result = submit_order_request(
            context=ExecutionContext(
                dry_run=False,
                trading_env="demo",
                enable_live_trading=False,
                require_approval=True,
            ),
            symbol="005930",
            name="Samsung",
            action="buy",
            qty=1,
            price=70000,
            reason="test",
            source="scheduler",
            execute_order_fn=lambda *_args: {"rt_cd": "0", "msg1": "ok"},
            save_trade_fn=lambda *args: saved.append(args),
            queue_order_fn=lambda *args: queued.append(args) or 123,
        )

        self.assertEqual(result.decision, "queue")
        self.assertEqual(result.approval_id, 123)
        self.assertEqual(len(queued), 1)
        self.assertEqual(saved, [])

    def test_submit_order_request_rejects_invalid_order_before_queue_or_execute(self):
        queued = []
        saved = []
        executed = []

        result = submit_order_request(
            context=ExecutionContext(
                dry_run=False,
                trading_env="demo",
                enable_live_trading=False,
                require_approval=True,
            ),
            symbol="005930",
            name="Samsung",
            action="buy",
            qty=0,
            price=70000,
            reason="test",
            source="scheduler",
            execute_order_fn=lambda *args: executed.append(args) or {"rt_cd": "0", "msg1": "ok"},
            save_trade_fn=lambda *args: saved.append(args),
            queue_order_fn=lambda *args: queued.append(args) or 123,
        )

        self.assertEqual(result.decision, "reject")
        self.assertIn("qty", result.response_msg)
        self.assertEqual(executed, [])
        self.assertEqual(queued, [])
        self.assertEqual(saved, [])

    def test_normalize_run_mode_validates_input(self):
        self.assertEqual(normalize_run_mode("analysis_only"), "analysis_only")
        with self.assertRaises(ValueError):
            normalize_run_mode("invalid")


if __name__ == "__main__":
    unittest.main()
