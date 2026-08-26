from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MarketRegime(str, Enum):
    BULL = "bull"
    BULL_PULLBACK = "bull_pullback"
    SIDEWAYS_LOW_VOL = "sideways_low_vol"
    SIDEWAYS_HIGH_VOL = "sideways_high_vol"
    BEAR_RALLY = "bear_rally"
    BEAR = "bear"
    CRASH = "crash"
    INSUFFICIENT_DATA = "insufficient_data"


class DataQuality(str, Enum):
    GOOD = "good"
    DEGRADED = "degraded"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class IndexFeatures:
    code: str
    session_date: str
    observations: int
    close: float
    sma20: float
    sma60: float
    sma200: float
    return_5d: float
    return_20d: float
    drawdown_20d: float
    volatility_20d: float
    volatility_120d: float
    volatility_ratio: float


@dataclass(frozen=True)
class BreadthFeatures:
    sample_size: int
    valid_count: int
    session_date: str
    advance_ratio: float
    above_sma20_ratio: float
    above_sma60_ratio: float
    failures: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RegimeSnapshot:
    market: str
    session_date: str
    evaluated_at: str
    regime: MarketRegime
    quality: DataQuality
    confidence: float
    risk_multiplier: float
    source: str
    indices: dict[str, IndexFeatures]
    breadth: BreadthFeatures
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def new_risk_allowed(self) -> bool:
        return self.quality is not DataQuality.INSUFFICIENT

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["regime"] = self.regime.value
        result["quality"] = self.quality.value
        result["new_risk_allowed"] = self.new_risk_allowed
        return result
