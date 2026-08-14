from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable

from .models import FuturesSignal, FuturesSignalRecord, VerificationResult


FUTURES_SIGNALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS futures_signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    received_at TEXT,
    raw_text TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profits TEXT NOT NULL,
    status TEXT NOT NULL,
    verification_outcome TEXT,
    verification_status TEXT,
    verification_hit_at TEXT,
    verification_hit_price REAL,
    verification_hit_target_index INTEGER,
    verification_requires_manual_review INTEGER NOT NULL DEFAULT 0,
    verification_reason TEXT,
    UNIQUE(source, source_message_id)
)
"""


class InMemoryFuturesSignalRepository:
    def __init__(self, records: Iterable[FuturesSignalRecord] = ()) -> None:
        self._records: OrderedDict[str, FuturesSignalRecord] = OrderedDict()
        self._source_index: dict[tuple[str, str], str] = {}
        for record in records:
            self.upsert(record.signal, verification=record.verification, metadata=record.metadata)

    def upsert(
        self,
        signal: FuturesSignal,
        *,
        verification: VerificationResult | None = None,
        metadata: dict | None = None,
    ) -> FuturesSignalRecord:
        record = FuturesSignalRecord(signal=signal, verification=verification, metadata=dict(metadata or {}))
        existing_id = self._source_index.get((signal.source, signal.source_message_id))
        if existing_id and existing_id != signal.id:
            self._records.pop(existing_id, None)
        self._records[signal.id] = record
        self._source_index[(signal.source, signal.source_message_id)] = signal.id
        return record

    def get(self, signal_id: str) -> FuturesSignalRecord | None:
        return self._records.get(signal_id)

    def find_by_source_message(self, source: str, source_message_id: str) -> FuturesSignalRecord | None:
        signal_id = self._source_index.get((source, source_message_id))
        if signal_id is None:
            return None
        return self.get(signal_id)

    def list(self, *, limit: int | None = None) -> list[FuturesSignalRecord]:
        records = list(reversed(self._records.values()))
        if limit is None:
            return records
        return records[:limit]
