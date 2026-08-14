"""Feature generation helpers for rule and AI strategy scoring."""
from __future__ import annotations

from typing import Any

from src.strategy.indicators import calc_bollinger, calc_macd, calc_rsi, calc_sma

FEATURE_VERSION = "features_v1"

MODEL_FEATURE_COLUMNS = [
    "strategy_score",
    "rsi",
    "rsi2",
    "macd_hist",
    "sma20_gap",
    "sma60_gap",
    "bb_position",
    "return_5d",
    "return_20d",
    "volatility_20d",
    "volume_ratio_20d",
    "max_drawdown_20d",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _return_over(prices: list[float], days: int) -> float:
    if len(prices) <= days:
        return 0.0
    base = prices[-days - 1]
    if base <= 0:
        return 0.0
    return (prices[-1] / base) - 1


def _volatility(prices: list[float], period: int = 20) -> float:
    if len(prices) < period + 1:
        return 0.0
    window = prices[-(period + 1):]
    returns = []
    for idx in range(1, len(window)):
        prev = window[idx - 1]
        if prev > 0:
            returns.append((window[idx] / prev) - 1)
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    return (sum((value - mean) ** 2 for value in returns) / len(returns)) ** 0.5


def _max_drawdown(prices: list[float], period: int = 20) -> float:
    window = prices[-period:] if len(prices) >= period else prices[:]
    if not window:
        return 0.0
    peak = window[0]
    max_dd = 0.0
    for price in window:
        peak = max(peak, price)
        if peak > 0:
            max_dd = min(max_dd, (price / peak) - 1)
    return abs(max_dd)


def build_strategy_features(
    prices: list[float],
    highs: list[float] | None = None,
    volumes: list[float] | None = None,
    *,
    strategy_score: float = 0.0,
) -> dict[str, float | str]:
    """Build stable model-ready features from OHLCV history.

    The function is deterministic and accepts short histories by returning
    neutral defaults, so dashboard scans and tests can use it without special
    casing model availability.
    """
    highs = highs or prices
    volumes = volumes or []
    current = prices[-1] if prices else 0.0
    sma20 = calc_sma(prices, 20)
    sma60 = calc_sma(prices, 60)
    bb_lo, bb_mid, bb_hi = calc_bollinger(prices, 20)
    macd = calc_macd(prices)

    bb_width = bb_hi - bb_lo
    if bb_width > 0:
        bb_position = (current - bb_lo) / bb_width
    else:
        bb_position = 0.5

    if len(volumes) >= 21:
        vol_avg = sum(volumes[-21:-1]) / 20
        volume_ratio = (volumes[-1] / vol_avg) if vol_avg > 0 else 0.0
    else:
        volume_ratio = 0.0

    return {
        "feature_version": FEATURE_VERSION,
        "strategy_score": _safe_float(strategy_score),
        "rsi": calc_rsi(prices, 14),
        "rsi2": calc_rsi(prices, 2),
        "macd_hist": _safe_float(macd.get("hist")),
        "sma20_gap": ((current / sma20) - 1) if current > 0 and sma20 > 0 else 0.0,
        "sma60_gap": ((current / sma60) - 1) if current > 0 and sma60 > 0 else 0.0,
        "bb_position": max(0.0, min(1.0, bb_position)),
        "return_5d": _return_over(prices, 5),
        "return_20d": _return_over(prices, 20),
        "volatility_20d": _volatility(prices, 20),
        "volume_ratio_20d": volume_ratio,
        "max_drawdown_20d": _max_drawdown(prices, 20),
    }


def feature_vector(features: dict[str, Any]) -> list[float]:
    """Return model feature values in a stable column order."""
    return [_safe_float(features.get(column)) for column in MODEL_FEATURE_COLUMNS]


def feature_contributions(features: dict[str, Any], limit: int = 3) -> list[dict[str, float | str]]:
    """Small explanation payload for dashboard display and logs."""
    scored: list[tuple[str, float]] = []
    for column in MODEL_FEATURE_COLUMNS:
        value = _safe_float(features.get(column))
        scored.append((column, abs(value)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [{"name": name, "value": _safe_float(features.get(name))} for name, _ in scored[:limit]]
