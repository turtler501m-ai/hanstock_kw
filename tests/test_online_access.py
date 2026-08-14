import unittest
import os
from unittest.mock import Mock, patch

from src.config import config
from src.execution_service import ExecutionContext, resolve_execution_decision
from src.online_access import OnlineAccessBlockedError


class OnlineAccessTests(unittest.TestCase):
    def setUp(self):
        self.original_blocked = config.online_access_blocked
        self.original_env_blocked = os.environ.get("ONLINE_ACCESS_BLOCKED")

    def tearDown(self):
        config.online_access_blocked = self.original_blocked
        if self.original_env_blocked is None:
            os.environ.pop("ONLINE_ACCESS_BLOCKED", None)
        else:
            os.environ["ONLINE_ACCESS_BLOCKED"] = self.original_env_blocked

    def test_execution_policy_rejects_before_approval_or_dry_run(self):
        decision = resolve_execution_decision(
            ExecutionContext(
                dry_run=True,
                trading_env="demo",
                enable_live_trading=False,
                require_approval=True,
                online_access_blocked=True,
            )
        )

        self.assertEqual(decision.decision, "reject")
        self.assertIn("online access", decision.reason)

    def test_kis_client_does_not_fetch_token_when_blocked(self):
        config.online_access_blocked = True
        from src.api import kis_api

        with patch.object(kis_api.HTTP, "post") as post:
            with self.assertRaises(OnlineAccessBlockedError):
                kis_api.KIStockAPI()

        post.assert_not_called()

    def test_trader_kis_client_is_blocked_even_with_token_cache(self):
        config.online_access_blocked = True
        import src.trader as trader

        with patch.object(trader.HTTP, "post") as post:
            with self.assertRaises(OnlineAccessBlockedError):
                trader.KIStockAPI()

        post.assert_not_called()

    def test_snapshot_returns_stale_db_value_without_refresh(self):
        config.online_access_blocked = True
        import src.dashboard as dashboard

        snapshot = {
            "payload": {"total_eval": 1234},
            "captured_at": "2026-06-01T09:00:00+09:00",
        }
        builder = Mock(side_effect=AssertionError("builder must not run"))
        with patch("src.db.repository.load_account_snapshot", return_value=snapshot):
            result = dashboard.snapshot_read_through("balance", builder)

        self.assertEqual(result["total_eval"], 1234)
        self.assertTrue(result["_snapshot"]["stale"])
        self.assertTrue(result["_snapshot"]["offline"])
        builder.assert_not_called()

    def test_pending_approval_is_not_claimed_when_blocked(self):
        config.online_access_blocked = True
        import src.dashboard as dashboard

        with patch.object(dashboard, "_claim_pending_approval") as claim:
            with self.assertRaises(dashboard.HTTPException) as raised:
                dashboard._approve_pending_approval(10)

        self.assertEqual(raised.exception.status_code, 409)
        claim.assert_not_called()
