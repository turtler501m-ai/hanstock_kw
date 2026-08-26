from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .classifier import classify_kr
from .kiwoom_kr import KiwoomKrCollector
from .models import DataQuality, MarketRegime, RegimeSnapshot
from .quality import assess_quality
from .repository import MarketRegimeRepository

KST = ZoneInfo("Asia/Seoul")


class MarketRegimeService:
    def __init__(self, broker: Any, *, repository: MarketRegimeRepository | None = None,
                 collector: KiwoomKrCollector | None = None, clock: Callable[[], datetime] | None = None):
        self.collector = collector or KiwoomKrCollector(broker)
        self.repository = repository or MarketRegimeRepository()
        self.clock = clock or (lambda: datetime.now(KST))

    def refresh(self, market: str = "KR") -> dict[str, Any]:
        if str(market).upper() != "KR":
            raise ValueError("Kiwoom market regime currently supports KR only")
        indices, breadth = self.collector.collect()
        quality = assess_quality(indices, breadth)
        warnings = list(quality.reasons if quality.quality is not DataQuality.GOOD else ())
        if getattr(self.collector, "index_failures", None):
            warnings.extend(f"{key}:{value}" for key, value in self.collector.index_failures.items())
        if quality.quality is DataQuality.INSUFFICIENT:
            regime, reasons, confidence, risk = MarketRegime.INSUFFICIENT_DATA, quality.reasons, 0.0, 0.0
        else:
            decision = classify_kr(indices, breadth)
            regime, reasons = decision.regime, decision.reasons + quality.reasons
            coverage = breadth.valid_count / max(1, breadth.sample_size)
            confidence = round(min(.95, .55 + .4 * coverage) * (1 if quality.quality is DataQuality.GOOD else .75), 3)
            risk = 1.0 if quality.quality is DataQuality.GOOD else .5
        now = self.clock().astimezone(KST)
        snapshot = RegimeSnapshot(
            market="KR", session_date=max((item.session_date for item in indices.values()), default=now.date().isoformat()), evaluated_at=now.isoformat(),
            regime=regime, quality=quality.quality, confidence=confidence, risk_multiplier=risk,
            source="kiwoom", indices=indices, breadth=breadth, reasons=tuple(reasons), warnings=tuple(warnings),
        ).to_dict()
        snapshot["snapshot_id"] = self.repository.save(snapshot)
        return snapshot

    def current(self) -> dict[str, Any] | None:
        return self.repository.current()

    def history(self, days: int = 30) -> list[dict[str, Any]]:
        return self.repository.history(max(1, min(int(days), 365)))

    def diagnostics(self) -> dict[str, Any]:
        current = self.current()
        return {"available": current is not None, "current": current,
                "checks": self._checks(current) if current else [{"name": "snapshot", "ok": False, "detail": "not_collected"}]}

    @staticmethod
    def _checks(value: dict[str, Any]) -> list[dict[str, Any]]:
        breadth = value.get("breadth", {})
        indices = value.get("indices", {})
        return [
            {"name": "quality", "ok": value.get("quality") != "insufficient", "detail": value.get("quality")},
            {"name": "index_history", "ok": all(v.get("observations", 0) >= 200 for v in indices.values()), "detail": {k: v.get("observations") for k, v in indices.items()}},
            {"name": "breadth_coverage", "ok": breadth.get("valid_count", 0) >= 60, "detail": f"{breadth.get('valid_count', 0)}/{breadth.get('sample_size', 0)}"},
            {"name": "new_risk_gate", "ok": bool(value.get("new_risk_allowed")), "detail": value.get("risk_multiplier")},
        ]


KrMarketRegimeService = MarketRegimeService
