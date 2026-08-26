from __future__ import annotations

from typing import Any

from src.db import ai_snapshot_repository

SOURCE = "kiwoom_kr_market_regime"


class MarketRegimeRepository:
    def save(self, snapshot: dict[str, Any]) -> int:
        return ai_snapshot_repository.create_market_snapshot({
            "snapshot_key": f"kr-regime:{snapshot['session_date']}:{snapshot['evaluated_at']}",
            "market": "KR", "source": SOURCE, "data_as_of": snapshot["session_date"],
            "regime": snapshot["regime"], "payload": snapshot,
        })

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        # This table also contains unrelated AI snapshots. Scan its bounded maximum
        # so a busy decision pipeline cannot hide the latest regime record.
        rows = ai_snapshot_repository.list_market_snapshots(market="KR", limit=1000)
        return [row["payload"] for row in rows if row.get("source") == SOURCE][:limit]

    def current(self) -> dict[str, Any] | None:
        rows = self.history(1)
        return rows[0] if rows else None
