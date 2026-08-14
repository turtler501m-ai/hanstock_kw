from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .models import FuturesSignalRecord, OhlcCandle, VerificationResult
from .parser import parse_futures_signal
from .repository import InMemoryFuturesSignalRepository
from .verifier import verify_signal


class FuturesSignalService:
    def __init__(self, repository: InMemoryFuturesSignalRepository | None = None) -> None:
        self.repository = repository or InMemoryFuturesSignalRepository()

    def ingest_message(
        self,
        text: str,
        *,
        source: str = "telegram",
        source_message_id: str | None = None,
        received_at: datetime | None = None,
    ) -> FuturesSignalRecord:
        signal = parse_futures_signal(
            text,
            source=source,
            source_message_id=source_message_id,
            received_at=received_at,
        )
        duplicate = self.repository.find_by_source_message(signal.source, signal.source_message_id) is not None
        return self.repository.upsert(signal, metadata={"duplicate": duplicate})

    def verify(self, signal_id: str, candles: Iterable[OhlcCandle]) -> FuturesSignalRecord | None:
        record = self.repository.get(signal_id)
        if record is None:
            return None
        result = verify_signal(record.signal, candles)
        return self.repository.upsert(record.signal, verification=result, metadata=record.metadata)

    def upsert_verification(
        self,
        signal_id: str,
        verification: VerificationResult,
    ) -> FuturesSignalRecord | None:
        record = self.repository.get(signal_id)
        if record is None:
            return None
        return self.repository.upsert(record.signal, verification=verification, metadata=record.metadata)

    def list_records(self, *, limit: int | None = None) -> list[FuturesSignalRecord]:
        return self.repository.list(limit=limit)

    def upsert(self, signal: "FuturesSignal", **kwargs) -> FuturesSignalRecord:
        return self.repository.upsert(signal, **kwargs)
