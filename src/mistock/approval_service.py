from __future__ import annotations

from src.approval_service import ApprovalService
from src.mistock import db
from src.repositories import ApprovalRepository


repository = ApprovalRepository(db.connect_db)
service = ApprovalService(repository, now_fn=db.now_text, market="US")


def get_approval_service() -> ApprovalService:
    """Return the canonical approval service backed by the Mistock database."""
    return service
