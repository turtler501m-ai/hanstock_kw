from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


Direction = Literal["long", "short"]
VerificationOutcome = Literal["pending", "tp", "sl", "ambiguous", "no_hit", "invalid"]


@dataclass(frozen=True)
class FuturesSignal:
    id: str
    source: str
    source_message_id: str
    received_at: datetime | None
    raw_text: str
    symbol: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profits: tuple[float, ...]
    qty: int = 1
    status: str = "parsed"

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["received_at"] = self.received_at.isoformat() if self.received_at else None
        record["take_profits"] = list(self.take_profits)
        return record


@dataclass(frozen=True)
class OhlcCandle:
    timestamp: datetime | str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class VerificationResult:
    outcome: VerificationOutcome
    status: str
    hit_at: datetime | str | None = None
    hit_price: float | None = None
    hit_target_index: int | None = None
    requires_manual_review: bool = False
    reason: str = ""

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FuturesSignalRecord:
    signal: FuturesSignal
    verification: VerificationResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        payload = self.signal.to_record()
        payload["verification"] = self.verification.to_record() if self.verification else None
        payload["metadata"] = dict(self.metadata)
        return payload
