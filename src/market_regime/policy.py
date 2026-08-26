from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


CANONICAL_REGIMES = frozenset({
    "bull", "bull_pullback", "sideways_low_vol", "sideways_high_vol",
    "bear_rally", "bear", "crash",
})

LEGACY_REGIME_ALIASES = {
    "neutral": frozenset({"sideways_low_vol", "sideways_high_vol"}),
    "low_volatility": frozenset({"bull", "bull_pullback", "sideways_low_vol"}),
    "high_volatility": frozenset({"sideways_high_vol"}),
    "bullish": frozenset({"bull", "bull_pullback"}),
    "bearish": frozenset({"bear", "crash"}),
    "sideways": frozenset({"sideways_low_vol", "sideways_high_vol"}),
}

REGIME_RISK_CAPS = {
    "bull": 1.0,
    "bull_pullback": 0.8,
    "sideways_low_vol": 0.6,
    "sideways_high_vol": 0.4,
    "bear_rally": 0.3,
    "bear": 0.0,
    "crash": 0.0,
}


@dataclass(frozen=True)
class NewRiskPolicy:
    allowed: bool
    regime: str
    quality: str
    multiplier: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "regime": self.regime,
            "quality": self.quality,
            "multiplier": self.multiplier,
            "reason": self.reason,
        }


def expand_allowed_regimes(values: Iterable[Any] | None) -> frozenset[str]:
    expanded: set[str] = set()
    for value in values or ():
        key = str(value or "").strip().lower()
        if key in CANONICAL_REGIMES:
            expanded.add(key)
        expanded.update(LEGACY_REGIME_ALIASES.get(key, ()))
    return frozenset(expanded)


def evaluate_new_risk(
    snapshot: Mapping[str, Any] | None,
    allowed_regimes: Iterable[Any] | None,
) -> NewRiskPolicy:
    if not isinstance(snapshot, Mapping):
        return NewRiskPolicy(False, "unknown", "insufficient", 0.0, "market_regime_missing")
    regime = str(snapshot.get("regime") or "unknown").strip().lower()
    quality = str(snapshot.get("quality") or "insufficient").strip().lower()
    try:
        multiplier = max(0.0, min(1.0, float(snapshot.get("risk_multiplier") or 0.0)))
    except (TypeError, ValueError):
        multiplier = 0.0
    if quality == "insufficient" or not bool(snapshot.get("new_risk_allowed")):
        return NewRiskPolicy(False, regime, quality, 0.0, "market_regime_insufficient")
    if quality not in {"good", "degraded"} or regime not in CANONICAL_REGIMES:
        return NewRiskPolicy(False, regime, quality, 0.0, "market_regime_invalid")
    try:
        evaluated_at = datetime.fromisoformat(
            str(snapshot.get("evaluated_at") or "").replace("Z", "+00:00")
        )
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("timezone required")
        age_seconds = (datetime.now(timezone.utc) - evaluated_at.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return NewRiskPolicy(False, regime, quality, 0.0, "market_regime_time_invalid")
    if age_seconds < -300 or age_seconds > 4 * 24 * 60 * 60:
        return NewRiskPolicy(False, regime, quality, 0.0, "market_regime_stale")
    allowed = expand_allowed_regimes(allowed_regimes)
    if regime not in allowed:
        return NewRiskPolicy(False, regime, quality, 0.0, "market_regime_not_allowed")
    multiplier = min(multiplier, REGIME_RISK_CAPS[regime])
    if multiplier <= 0:
        return NewRiskPolicy(False, regime, quality, 0.0, "market_regime_zero_risk")
    return NewRiskPolicy(True, regime, quality, multiplier, "market_regime_allowed")
