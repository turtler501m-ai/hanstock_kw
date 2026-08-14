from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import sqlite3

from src.futures_signals import db as futures_signals_db
from src.utils.logger import logger


class FuturesDashboardService:
    def __init__(self, now_fn: Callable[[], datetime]) -> None:
        self.now_fn = now_fn

    def list_persisted_signals(self, limit: int | None = 100) -> list[dict]:
        try:
            rows = futures_signals_db.list_signals(limit=limit or 500)
        except (sqlite3.DatabaseError, OSError, ValueError, TypeError) as exc:
            logger.warning(f"Failed to load futures signals: {exc}")
            return []
        return [self.db_signal_to_api(row) for row in rows]

    def db_signal_to_api(self, row: dict) -> dict:
        direction = str(row.get("direction") or "").lower()
        is_exit = direction == "exit"
        message_id = str(row.get("message_id") or row.get("id") or "")
        channel_key = str(row.get("channel_key") or "telegram")
        try:
            confidence = row.get("confidence")
            confidence_value = float(confidence) if confidence is not None else 0.65
        except (TypeError, ValueError):
            confidence_value = 0.65
        target_price = row.get("target_price")
        targets = [] if target_price in (None, "") else [target_price]
        return {
            "id": f"{channel_key}-{message_id}",
            "internal_id": f"{channel_key}-{message_id}",
            "received_at": row.get("message_date"),
            "source": channel_key,
            "channel": channel_key,
            "symbol": row.get("symbol") or "-",
            "market": self.market_name(str(row.get("symbol") or "")),
            "side": "exit" if is_exit else ("buy" if direction == "long" else "sell"),
            "direction": direction or "-",
            "entry": row.get("entry_price"),
            "entry_price": row.get("entry_price"),
            "stop": row.get("stop_loss"),
            "stop_loss": row.get("stop_loss"),
            "targets": targets,
            "take_profit_1": target_price,
            "confidence": confidence_value,
            "parse_status": "parsed",
            "status": "parsed",
            "verification_status": "pending",
            "verification": {
                "status": "pending",
                "outcome": "pending",
                "hit_at": None,
                "hit_price": None,
                "hit_target_index": None,
                "reason": row.get("notes") or "",
                "rule_match": True,
                "risk_reward": None,
                "duplicate": False,
                "requires_manual_review": False,
            },
            "raw_text": row.get("raw_text") or "",
        }

    @staticmethod
    def market_name(symbol: str) -> str:
        letters = "".join(char for char in symbol if char.isalpha())
        has_contract_year = any(char.isdigit() for char in symbol)
        root = letters[:-1] if has_contract_year and len(letters) > 1 else letters
        names = {
            "MNQ": "CME Micro E-mini Nasdaq-100",
            "NQ": "CME E-mini Nasdaq-100",
            "MES": "CME Micro E-mini S&P 500",
            "ES": "CME E-mini S&P 500",
            "MCL": "NYMEX Micro WTI Crude Oil",
            "CL": "NYMEX WTI Crude Oil",
            "MGC": "COMEX Micro Gold",
            "GC": "COMEX Gold",
        }
        return names.get(root, "Overseas futures")

    def summarize(
        self,
        records: list,
        *,
        converter: Callable[[object], dict],
        telegram_connected: bool = False,
    ) -> dict:
        signals = [record if isinstance(record, dict) else converter(record) for record in records]
        status_counts: dict[str, int] = {}
        for signal in signals:
            status = signal["status"]
            status_counts[status] = status_counts.get(status, 0) + 1
        latest = max(
            (signal["received_at"] for signal in signals if signal.get("received_at")),
            default=None,
        )
        total = len(signals)
        verified = status_counts.get("verified", 0)
        needs_review = sum(
            status_counts.get(status, 0)
            for status in ("needs_review", "pending", "parsed")
        )
        rejected = status_counts.get("rejected", 0)
        confidence_values = [
            signal["confidence"]
            for signal in signals
            if signal.get("confidence") is not None
        ]
        avg_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else None
        )
        win_rate = verified / (verified + rejected) if verified + rejected else None
        recent = signals[:20]
        return {
            "as_of": self.now_fn().isoformat(),
            "source": "service",
            "telegram_connected": telegram_connected,
            "total": total,
            "verified": verified,
            "needs_review": needs_review,
            "rejected": rejected,
            "parse_success_rate": 1.0 if total else None,
            "win_rate": win_rate,
            "avg_parse_confidence": avg_confidence,
            "avg_pnl_points": None,
            "status_counts": status_counts,
            "latest_signal_at": latest,
            "performance": {
                "labels": [str(item.get("received_at") or "")[11:16] for item in recent][::-1],
                "pnl": [0 for _ in recent][::-1],
                "win_rate": [0 for _ in recent][::-1],
            },
        }
