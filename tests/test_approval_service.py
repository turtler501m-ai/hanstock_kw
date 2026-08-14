import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.approval_service import (
    ApprovalCreateRequest,
    ApprovalNotFoundError,
    ApprovalService,
    ApprovalStatusError,
)
from src.repositories import ApprovalRepository


class ApprovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "approvals.sqlite"
        self.repository = ApprovalRepository(self._connect_db)
        self.now_value = "2026-04-26 09:00:00"
        self.service = ApprovalService(self.repository, now_fn=lambda: self.now_value)
        self.service.init_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _connect_db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def test_create_approval_persists_pending_row(self):
        approval_id = self.service.create_approval(
            ApprovalCreateRequest(
                symbol="005930",
                name="Samsung",
                action="buy",
                qty=3,
                price=70000,
                reason="rebalance",
                source="dashboard",
                strategy_id="strategy_a",
                strategy_version=2,
                profile_hash="hash-a",
                source_candidate_id=7,
            )
        )

        approval = self.service.get_approval(approval_id)
        self.assertEqual(approval.status, "pending")
        self.assertEqual(approval.response_msg, "")
        self.assertEqual(approval.created_at, self.now_value)
        self.assertEqual(approval.updated_at, self.now_value)
        self.assertEqual(approval.symbol, "005930")
        self.assertEqual(approval.strategy_id, "strategy_a")
        self.assertEqual(approval.strategy_version, 2)
        self.assertEqual(approval.profile_hash, "hash-a")
        self.assertEqual(approval.source_candidate_id, 7)

    def test_queue_approval_uses_scheduler_default_source(self):
        approval_id = self.service.queue_approval("000660", "SK", "sell", 1, 120000, "trim")

        approval = self.service.get_approval(approval_id)
        self.assertEqual(approval.source, "scheduler")

    def test_get_pending_approval_rejects_non_pending_status(self):
        approval_id = self.service.queue_approval("005930", "Samsung", "buy", 1, 70000, "test")
        self.now_value = "2026-04-26 09:30:00"
        self.service.update_status(approval_id, status="executed", response_msg="ok")

        with self.assertRaises(ApprovalStatusError):
            self.service.get_pending_approval(approval_id)

    def test_update_status_updates_response_message_and_timestamp(self):
        approval_id = self.service.queue_approval("005930", "Samsung", "buy", 1, 70000, "test")

        self.now_value = "2026-04-26 09:45:00"
        approval = self.service.update_status(approval_id, status="failed", response_msg="broker error")

        self.assertEqual(approval.status, "failed")
        self.assertEqual(approval.response_msg, "broker error")
        self.assertEqual(approval.updated_at, self.now_value)

    def test_reject_approval_marks_row_rejected(self):
        approval_id = self.service.queue_approval("005930", "Samsung", "buy", 1, 70000, "test")

        approval = self.service.reject_approval(approval_id)

        self.assertEqual(approval.status, "rejected")
        self.assertEqual(approval.response_msg, "Rejected by dashboard")

    def test_list_approvals_enforces_limits_and_ordering(self):
        first_id = self.service.queue_approval("005930", "Samsung", "buy", 1, 70000, "first")
        second_id = self.service.queue_approval("000660", "SK", "sell", 2, 120000, "second")

        approvals = self.service.list_approvals(limit=10)

        self.assertEqual([approval.id for approval in approvals], [second_id, first_id])
        with self.assertRaises(ValueError):
            self.service.list_approvals(limit=0)

    def test_get_approval_raises_for_missing_row(self):
        with self.assertRaises(ApprovalNotFoundError):
            self.service.get_approval(999)


if __name__ == "__main__":
    unittest.main()
