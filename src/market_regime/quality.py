from __future__ import annotations

from dataclasses import dataclass

from .models import BreadthFeatures, DataQuality, IndexFeatures


@dataclass(frozen=True)
class QualityDecision:
    quality: DataQuality
    reasons: tuple[str, ...]


def assess_quality(indices: dict[str, IndexFeatures], breadth: BreadthFeatures) -> QualityDecision:
    reasons: list[str] = []
    required = {"kospi", "kosdaq"}
    missing = sorted(required - set(indices))
    if missing:
        reasons.append("missing_indices:" + ",".join(missing))
    short = sorted(name for name, value in indices.items() if value.observations < 200)
    if short:
        reasons.append("short_index_history:" + ",".join(short))
    dates = {value.session_date for value in indices.values() if value.session_date}
    if len(dates) > 1:
        reasons.append("index_session_date_mismatch")
    breadth_date_mismatch = bool(dates and breadth.session_date not in dates)
    if breadth_date_mismatch:
        reasons.append("breadth_session_date_mismatch")
    if breadth.valid_count < 30:
        reasons.append("breadth_valid_count_below_30")
    if missing or short or len(dates) > 1 or breadth_date_mismatch or breadth.valid_count < 30:
        return QualityDecision(DataQuality.INSUFFICIENT, tuple(reasons))

    success_rate = breadth.valid_count / max(1, breadth.sample_size)
    if breadth.valid_count < 60 or success_rate < 0.8:
        reasons.append("breadth_coverage_degraded")
        return QualityDecision(DataQuality.DEGRADED, tuple(reasons))
    return QualityDecision(DataQuality.GOOD, ("required_market_data_available",))
