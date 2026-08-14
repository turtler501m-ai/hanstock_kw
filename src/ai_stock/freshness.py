# -*- coding: utf-8 -*-
"""데이터 신선도/stale 판정과 TTL (§9·§16 캐시).

TTL은 데이터 종류별로 분리하고 환경설정에서 변경 가능하다(§1.4).
stale 데이터는 confirmed·실행 계획·자동 타이밍에 사용하지 못한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

KST = timezone(timedelta(hours=9))

# 데이터 종류별 기본 TTL(분). 환경변수 AI_STOCK_TTL_<KIND>_MIN으로 override.
DEFAULT_TTL_MIN: dict[str, int] = {
    "intraday_price": 5,
    "daily_bar": 1440,
    "market_regime": 720,
    "narrative": 720,
    "ai_eval": 360,
    "briefing_monthly": 43200,
    "briefing_weekly": 10080,
    "briefing_daily": 1440,
    "universe": 1440,
}


def ttl_minutes(kind: str) -> int:
    """종류별 TTL(분). 환경변수 우선(§1.4 설정 가능성)."""
    env = os.environ.get(f"AI_STOCK_TTL_{kind.upper()}_MIN")
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    return DEFAULT_TTL_MIN.get(kind, 1440)


def parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def now() -> datetime:
    return datetime.now(KST)


def age_minutes(data_as_of: Any, *, ref: datetime | None = None) -> float | None:
    dt = parse_ts(data_as_of)
    if dt is None:
        return None
    ref = ref or now()
    return (ref - dt).total_seconds() / 60.0


def is_stale(data_as_of: Any, kind: str, *, ref: datetime | None = None) -> bool:
    """기준시각이 없거나 TTL을 초과하면 stale로 본다(보수적)."""
    age = age_minutes(data_as_of, ref=ref)
    if age is None:
        return True
    return age > ttl_minutes(kind)


def freshness_meta(data_as_of: Any, kind: str) -> dict[str, Any]:
    age = age_minutes(data_as_of)
    return {
        "data_as_of": str(data_as_of) if data_as_of else None,
        "kind": kind,
        "ttl_min": ttl_minutes(kind),
        "age_min": round(age, 1) if age is not None else None,
        "stale": is_stale(data_as_of, kind),
    }
