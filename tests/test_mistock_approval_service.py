import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.approval_service import ApprovalService, ApprovalStatusError
from src.mistock import approval_service as mistock_approval
from src.repositories import ApprovalRepository


class MistockApprovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "mistock-approvals.sqlite"
        self.repository = ApprovalRepository(self._connect_db)
        self.now_value = "2026-08-21 10:30:00"
        self.service = ApprovalService(self.repository, now_fn=lambda: self.now_value)
        self.service.init_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _connect_db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5)

    def _queue(self, *, client_order_key: str = "") -> int:
        return self.service.queue_approval(
            "AAPL",
            "Apple",
            "buy",
            2,
            225.50,
            "mistock regression",
            source="mistock_scheduler",
            strategy_id="us-alpha",
            client_order_key=client_order_key,
        )

    def test_mistock_composes_the_common_approval_service(self):
        service = mistock_approval.get_approval_service()

        self.assertIs(service, mistock_approval.service)
        self.assertIsInstance(service, ApprovalService)
        self.assertIsInstance(mistock_approval.repository, ApprovalRepository)

    def test_client_order_key_makes_approval_creation_idempotent(self):
        first_id = self._queue(client_order_key="us-alpha:AAPL:buy:2026-08-21:v1")
        second_id = self._queue(client_order_key="us-alpha:AAPL:buy:2026-08-21:v1")

        self.assertEqual(second_id, first_id)
        approvals = self.service.list_approvals(limit=10)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].client_order_key, "us-alpha:AAPL:buy:2026-08-21:v1")

    def test_only_one_caller_can_claim_the_same_pending_approval(self):
        approval_id = self._queue(client_order_key="claim-once")

        claimed = self.service.transition_pending(
            approval_id,
            status="executing",
            response_msg="claimed by scheduler",
        )
        self.assertEqual(claimed.status, "executing")

        with self.assertRaises(ApprovalStatusError):
            self.service.transition_pending(
                approval_id,
                status="executing",
                response_msg="second claim",
            )
        self.assertEqual(self.service.get_approval(approval_id).status, "executing")

    def test_reject_only_accepts_a_pending_approval(self):
        pending_id = self._queue(client_order_key="reject-pending")
        rejected = self.service.reject_approval(pending_id, response_msg="operator rejected")

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(rejected.response_msg, "operator rejected")
        with self.assertRaises(ApprovalStatusError):
            self.service.reject_approval(pending_id)

        executing_id = self._queue(client_order_key="reject-executing")
        self.service.transition_pending(
            executing_id,
            status="executing",
            response_msg="claimed",
        )
        with self.assertRaises(ApprovalStatusError):
            self.service.reject_approval(executing_id)


if __name__ == "__main__":
    unittest.main()
