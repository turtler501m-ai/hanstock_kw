# -*- coding: utf-8 -*-
"""시장 구분 메타와 헬퍼 (§1.3·§3 시장 코드).

AI한스톡(KR)/AI미스톡(US)의 통화·지수·공급자 등을 분리한다.
두 계열 값을 직접 혼합하지 않는다.
"""
from __future__ import annotations

from typing import Any

from src.ai_stock.constants import (
    MARKET_ALL,
    MARKET_KR,
    MARKET_US,
    QUERY_MARKETS,
    STORABLE_MARKETS,
)

MARKET_META: dict[str, dict[str, Any]] = {
    MARKET_KR: {
        "code": MARKET_KR,
        "label": "AI한스톡",
        "country": "한국",
        "provider": "KIS",
        "currency": "KRW",
        "indices": ["KOSPI", "KOSDAQ"],
        "symbol_kind": "numeric6",
        "trading_hours": "09:00-15:30 KST",
    },
    MARKET_US: {
        "code": MARKET_US,
        "label": "AI미스톡",
        "country": "미국",
        "provider": "MISTOCK",
        "currency": "USD",
        "indices": ["S&P500", "Nasdaq", "Russell2000"],
        "symbol_kind": "ticker",
        "trading_hours": "US regular session",
    },
}


class MarketError(ValueError):
    """잘못된 시장 코드."""


def normalize_market(value: Any, *, default: str | None = None) -> str:
    """시장 코드를 대문자로 정규화. 허용되지 않으면 default 또는 예외."""
    raw = str(value or "").strip().upper()
    if raw in QUERY_MARKETS:
        return raw
    if default is not None:
        return default
    raise MarketError(f"market must be one of {QUERY_MARKETS}, got {value!r}")


def require_storable_market(value: Any) -> str:
    """저장용 시장 코드(KR/US)만 허용. ALL은 거부 (§4.1·§6.0)."""
    market = normalize_market(value)
    if market not in STORABLE_MARKETS:
        raise MarketError(f"storable market must be one of {STORABLE_MARKETS}, got {market}")
    return market


def is_all(value: Any) -> bool:
    return normalize_market(value, default=MARKET_ALL) == MARKET_ALL


def markets_for_query(value: Any) -> tuple[str, ...]:
    """조회 시장 → 실제 처리할 저장 시장 목록. ALL이면 (KR, US)."""
    market = normalize_market(value, default=MARKET_ALL)
    if market == MARKET_ALL:
        return STORABLE_MARKETS
    return (market,)


def currency_of(value: Any) -> str:
    return MARKET_META[require_storable_market(value)]["currency"]


def meta_of(value: Any) -> dict[str, Any]:
    return dict(MARKET_META[require_storable_market(value)])
