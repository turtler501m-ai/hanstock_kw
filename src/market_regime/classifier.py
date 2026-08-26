from __future__ import annotations

from dataclasses import dataclass

from .models import BreadthFeatures, IndexFeatures, MarketRegime


@dataclass(frozen=True)
class Classification:
    regime: MarketRegime
    reasons: tuple[str, ...]


def classify_index(value: IndexFeatures, breadth_ratio: float) -> Classification:
    p, vr = value.close, value.volatility_ratio
    if (value.drawdown_20d <= -0.12 or value.return_5d <= -0.08
            or (vr >= 2.5 and value.return_5d < -0.04)):
        return Classification(MarketRegime.CRASH, (f"{value.code}:crash_threshold",))
    if p > value.sma20 > value.sma60 > value.sma200 and value.return_20d > 0 and breadth_ratio >= .55 and vr < 1.3:
        return Classification(MarketRegime.BULL, (f"{value.code}:aligned_uptrend",))
    if p > value.sma60 > value.sma200 and p <= value.sma20 and value.return_20d > 0 and value.return_5d <= 0:
        return Classification(MarketRegime.BULL_PULLBACK, (f"{value.code}:long_uptrend_short_pullback",))
    if p < value.sma20 < value.sma60 < value.sma200 and value.return_20d < 0 and breadth_ratio <= .45:
        return Classification(MarketRegime.BEAR, (f"{value.code}:aligned_downtrend",))
    if p < value.sma60 < value.sma200 and p > value.sma20 and value.return_5d > 0:
        return Classification(MarketRegime.BEAR_RALLY, (f"{value.code}:short_rebound_in_downtrend",))
    regime = MarketRegime.SIDEWAYS_HIGH_VOL if vr >= 1.3 else MarketRegime.SIDEWAYS_LOW_VOL
    return Classification(regime, (f"{value.code}:complete_sideways_fallback",))


def classify_kr(indices: dict[str, IndexFeatures], breadth: BreadthFeatures) -> Classification:
    kospi = classify_index(indices["kospi"], breadth.above_sma20_ratio)
    kosdaq = classify_index(indices["kosdaq"], breadth.above_sma20_ratio)
    reasons = kospi.reasons + kosdaq.reasons
    if kospi.regime is MarketRegime.CRASH and kosdaq.regime in {MarketRegime.CRASH, MarketRegime.BEAR}:
        return Classification(MarketRegime.CRASH, reasons + ("confirmed_market_crash",))
    if kospi.regime is MarketRegime.BULL and kosdaq.regime in {MarketRegime.BULL, MarketRegime.BULL_PULLBACK}:
        return Classification(MarketRegime.BULL, reasons + ("broad_uptrend",))
    if kospi.regime is MarketRegime.BEAR and kosdaq.regime in {MarketRegime.BEAR, MarketRegime.CRASH}:
        return Classification(MarketRegime.BEAR, reasons + ("broad_downtrend",))
    if kospi.regime is MarketRegime.BULL_PULLBACK and kosdaq.regime not in {MarketRegime.BEAR, MarketRegime.CRASH}:
        return Classification(MarketRegime.BULL_PULLBACK, reasons)
    if kospi.regime is MarketRegime.BEAR_RALLY and kosdaq.regime not in {MarketRegime.BULL, MarketRegime.CRASH}:
        return Classification(MarketRegime.BEAR_RALLY, reasons)
    high_vol = max(indices["kospi"].volatility_ratio, indices["kosdaq"].volatility_ratio) >= 1.3
    divergent = {kospi.regime, kosdaq.regime} & {MarketRegime.BULL, MarketRegime.BEAR, MarketRegime.CRASH}
    regime = MarketRegime.SIDEWAYS_HIGH_VOL if high_vol or len(divergent) > 1 else MarketRegime.SIDEWAYS_LOW_VOL
    return Classification(regime, reasons + (("market_divergence",) if len(divergent) > 1 else ()))
