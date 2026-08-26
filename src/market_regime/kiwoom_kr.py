from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Protocol

from .models import BreadthFeatures, IndexFeatures

# Stable, liquid representatives. Configuration can replace this without coupling it
# to strategy candidates or current holdings.
KR_REGIME_BREADTH_UNIVERSE = (
    "005930", "000660", "373220", "207940", "005380", "000270", "068270", "005490", "035420", "035720",
    "051910", "006400", "105560", "055550", "086790", "316140", "012330", "028260", "066570", "003550",
    "034730", "096770", "017670", "030200", "009150", "010130", "018260", "032830", "024110", "000810",
    "035250", "011200", "010950", "047050", "090430", "097950", "004020", "011170", "021240", "161390",
    "247540", "086520", "196170", "145020", "091990", "263750", "058470", "039030", "067310", "293490",
    "112040", "214150", "357780", "041510", "035900", "253450", "095340", "078600", "036930", "098460",
)


class KiwoomMarketBroker(Protocol):
    def get_index_daily(self, index_code: str, n: int = 90) -> list[dict[str, Any]]: ...
    def fetch_daily_bars(self, symbol: str, count: int = 60) -> list[Any]: ...


def _value(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


def _returns(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]


def _vol(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def build_index_features(code: str, rows: list[Any]) -> IndexFeatures:
    normalized = sorted(rows, key=lambda row: str(_value(row, "date")))
    closes = [float(_value(row, "close" if isinstance(row, dict) else "close_price")) for row in normalized]
    if len(closes) < 200 or any(not math.isfinite(v) or v <= 0 for v in closes):
        raise ValueError("index history requires at least 200 positive closes")
    returns = _returns(closes)
    current = closes[-1]
    vol20, vol120 = _vol(returns[-20:]), _vol(returns[-120:])
    if vol120 <= 0:
        raise ValueError("baseline volatility must be positive")
    return IndexFeatures(
        code=code, session_date=str(_value(normalized[-1], "date")), observations=len(closes), close=current,
        sma20=sum(closes[-20:]) / 20, sma60=sum(closes[-60:]) / 60, sma200=sum(closes[-200:]) / 200,
        return_5d=current / closes[-6] - 1, return_20d=current / closes[-21] - 1,
        drawdown_20d=current / max(closes[-20:]) - 1, volatility_20d=vol20,
        volatility_120d=vol120, volatility_ratio=vol20 / vol120,
    )


class KiwoomKrCollector:
    def __init__(self, broker: KiwoomMarketBroker, universe: tuple[str, ...] = KR_REGIME_BREADTH_UNIVERSE):
        self.broker, self.universe = broker, universe
        self.index_failures: dict[str, str] = {}

    def collect(self) -> tuple[dict[str, IndexFeatures], BreadthFeatures]:
        indices: dict[str, IndexFeatures] = {}
        self.index_failures = {}
        for name, code in (("kospi", "0001"), ("kosdaq", "1001")):
            try:
                indices[name] = build_index_features(code, self.broker.get_index_daily(code, n=260))
            except Exception as exc:
                self.index_failures[name] = f"{type(exc).__name__}: {exc}"
        stats: list[tuple[bool, bool, bool, str]] = []
        failures: dict[str, str] = {}
        for symbol in self.universe:
            try:
                rows = sorted(self.broker.fetch_daily_bars(symbol, count=80), key=lambda row: str(_value(row, "date")))
                closes = [float(_value(row, "close" if isinstance(row, dict) else "close_price")) for row in rows]
                if len(closes) < 60 or min(closes) <= 0:
                    raise ValueError("fewer than 60 valid bars")
                stats.append((closes[-1] > closes[-2], closes[-1] > sum(closes[-20:]) / 20,
                              closes[-1] > sum(closes[-60:]) / 60, str(_value(rows[-1], "date"))))
            except Exception as exc:
                failures[symbol] = f"{type(exc).__name__}: {exc}"
        valid = len(stats)
        session_date = max((item[3] for item in stats), default="")
        same_date = [item for item in stats if item[3] == session_date]
        valid = len(same_date)
        denominator = max(1, valid)
        return indices, BreadthFeatures(
            sample_size=len(self.universe), valid_count=valid, session_date=session_date,
            advance_ratio=sum(v[0] for v in same_date) / denominator,
            above_sma20_ratio=sum(v[1] for v in same_date) / denominator,
            above_sma60_ratio=sum(v[2] for v in same_date) / denominator, failures=failures,
        )
