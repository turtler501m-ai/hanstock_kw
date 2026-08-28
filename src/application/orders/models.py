from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UnifiedOrderStatus(str, Enum):
    CREATED = "created"
    RISK_APPROVED = "risk_approved"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partial"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    BROKER_UNKNOWN = "broker_unknown"
    FAILED = "failed"


TERMINAL_STATUSES = {
    UnifiedOrderStatus.FILLED.value,
    UnifiedOrderStatus.CANCELED.value,
    UnifiedOrderStatus.REJECTED.value,
    UnifiedOrderStatus.EXPIRED.value,
}


ALLOWED_TRANSITIONS = {
    "created": {"risk_approved", "approval_pending", "approved", "rejected", "expired"},
    "risk_approved": {"approval_pending", "approved", "rejected", "expired"},
    "approval_pending": {"approved", "rejected", "expired"},
    "approved": {"submitting", "rejected", "expired"},
    "submitting": {"submitted", "broker_unknown", "rejected", "failed"},
    "submitted": {"open", "partial", "filled", "cancel_pending", "canceled", "rejected", "broker_unknown"},
    "open": {"partial", "filled", "cancel_pending", "canceled", "rejected", "broker_unknown"},
    "partial": {"partial", "filled", "cancel_pending", "canceled", "broker_unknown"},
    "cancel_pending": {"partial", "filled", "canceled", "broker_unknown"},
    "broker_unknown": {"submitted", "open", "partial", "filled", "canceled", "rejected"},
    "failed": {"rejected"},
}


@dataclass(frozen=True, slots=True)
class OrderIntent:
    client_order_key: str
    correlation_id: str
    symbol: str
    side: str
    quantity: float
    price: float = 0
    name: str = ""
    account_key: str = ""
    market: str = "KR"
    order_type: str = "limit"
    time_in_force: str = "DAY"
    strategy_id: str | None = None
    strategy_version: int | None = None
    signal_id: str | None = None
    decision_id: int | None = None
    approval_id: int | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.client_order_key.strip() or not self.correlation_id.strip():
            raise ValueError("client_order_key and correlation_id are required")
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if float(self.quantity) <= 0:
            raise ValueError("quantity must be positive")
