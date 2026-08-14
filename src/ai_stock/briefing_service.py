# -*- coding: utf-8 -*-
"""탭1 시장 브리핑 + 월/주/일 시황 파일 파이프라인 (§4.7·§5.1).

시황은 파일로 영속화(.runtime/ai_stock/briefings/{market}/)하고 1차 점수화의
컨텍스트로 재사용한다. 데이터가 없으면 임의 중립값을 만들지 않고 insufficient_data.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.ai_stock.constants import DATA_GOOD, DATA_INSUFFICIENT, MARKET_KR, MARKET_US
from src.ai_stock.freshness import KST, now as _now, is_stale
from src.ai_stock.markets import markets_for_query, require_storable_market

BASE_DIR = Path(__file__).resolve().parents[2]
BRIEFINGS_DIR = BASE_DIR / ".runtime" / "ai_stock" / "briefings"

# 국면 → 점수 multiplier (§4 시장 브리핑 → 후보 보정). [0.9, 1.1] 범위.
REGIME_MULTIPLIER = {
    "bullish": 1.1,
    "sideways": 1.0,
    "bearish": 0.9,
    "high_volatility": 0.95,
    "insufficient_data": 1.0,
}


def regime_multiplier(regime: str | None) -> float:
    return REGIME_MULTIPLIER.get(str(regime or "insufficient_data"), 1.0)


def _primary_index(market: str) -> str:
    return "KOSPI" if market == MARKET_KR else "S&P500"


def _pct(series: list[float], lookback: int) -> float | None:
    if not series or len(series) <= lookback:
        return None
    a, b = series[-1], series[-1 - lookback]
    if b == 0:
        return None
    return round((a / b - 1.0) * 100.0, 2)


def _volatility(series: list[float], window: int = 20) -> float | None:
    if not series or len(series) < window + 1:
        return None
    rets = []
    for i in range(len(series) - window, len(series)):
        if series[i - 1]:
            rets.append(series[i] / series[i - 1] - 1.0)
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return round((var ** 0.5) * 100.0, 2)


def compute_regime(market: str, index_series: dict[str, list[float]] | None) -> dict[str, Any]:
    """단일 시장 국면 산출 (§4 결과 구조). 데이터 부족 시 insufficient_data."""
    market = require_storable_market(market)
    series = (index_series or {}).get(_primary_index(market)) or []
    data_as_of = _now().isoformat()
    if len(series) < 21:
        return {
            "market": market,
            "regime": "insufficient_data",
            "regime_score": None,
            "trend_score": None,
            "volatility_score": None,
            "breadth_score": None,
            "summary": "지수 시계열 부족으로 국면을 판정할 수 없습니다.",
            "positive_factors": [],
            "risk_factors": ["insufficient_market_data"],
            "data_quality": DATA_INSUFFICIENT,
            "data_as_of": data_as_of,
        }
    r1, r5, r20 = _pct(series, 1), _pct(series, 5), _pct(series, 20)
    vol = _volatility(series)
    trend_score = max(0.0, min(100.0, 50.0 + (r20 or 0.0) * 3.0))
    vol_score = max(0.0, min(100.0, (vol or 0.0) * 20.0))
    if vol is not None and vol >= 2.5:
        regime = "high_volatility"
    elif (r20 or 0.0) > 2.0:
        regime = "bullish"
    elif (r20 or 0.0) < -2.0:
        regime = "bearish"
    else:
        regime = "sideways"
    return {
        "market": market,
        "regime": regime,
        "regime_score": round(trend_score, 1),
        "trend_score": round(trend_score, 1),
        "volatility_score": round(vol_score, 1),
        "breadth_score": None,
        "returns": {"d1": r1, "d5": r5, "d20": r20},
        "summary": f"{_primary_index(market)} 20일 {r20}% → {regime}",
        "positive_factors": ["uptrend"] if regime == "bullish" else [],
        "risk_factors": ["high_volatility"] if regime == "high_volatility" else [],
        "data_quality": DATA_GOOD,
        "data_as_of": data_as_of,
    }


def overview(market: str) -> dict[str, Any]:
    """탭1 overview: 시장별 국면 + 최신 시황 파일 요약 (§5.1)."""
    from src.ai_stock.market_data import get_provider

    provider = get_provider()
    regimes = []
    for m in markets_for_query(market):
        regime = compute_regime(m, provider.index_series(m))
        regime["latest_briefing"] = _latest_summary(m)
        regimes.append(regime)
    return {"regimes": regimes}


def _market_dir(market: str, period: str) -> Path:
    return BRIEFINGS_DIR / require_storable_market(market) / period


def _period_key(period: str, dt: datetime) -> str:
    if period == "monthly":
        return dt.strftime("%Y-%m")
    if period == "weekly":
        return dt.strftime("%Y-W%V")
    return dt.strftime("%Y-%m-%d")


def generate(market: str, period: str = "daily") -> dict[str, Any]:
    """시황 파일(json+md) 생성 (§4.7)."""
    from src.ai_stock.market_data import get_provider

    market = require_storable_market(market)
    if period not in ("monthly", "weekly", "daily"):
        period = "daily"
    regime = compute_regime(market, get_provider().index_series(market))
    dt = datetime.now(KST)
    key = _period_key(period, dt)
    payload = {
        "market": market,
        "period": period,
        "key": key,
        "generated_at": dt.isoformat(),
        "regime": regime,
        "watch_points": [],
    }
    out_dir = _market_dir(market, period)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{key}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = f"# {market} 시황 ({period} {key})\n\n- 국면: {regime['regime']}\n- 요약: {regime['summary']}\n- 기준시각: {regime['data_as_of']}\n"
    (out_dir / f"{key}.md").write_text(md, encoding="utf-8")
    return payload


def list_briefings(market: str, period: str = "daily") -> dict[str, Any]:
    out_dir = _market_dir(market, period)
    files = []
    if out_dir.exists():
        for p in sorted(out_dir.glob("*.json"), reverse=True):
            files.append(p.stem)
    return {"market": require_storable_market(market), "period": period, "briefings": files}


def get_briefing(market: str, period: str, key: str) -> dict[str, Any]:
    market = require_storable_market(market)
    if period not in ("monthly", "weekly", "daily"):
        raise ValueError("invalid briefing period")
    safe_key = Path(str(key)).name
    if safe_key != key or not safe_key:
        raise ValueError("invalid briefing key")
    path = _market_dir(market, period) / f"{safe_key}.json"
    if not path.exists():
        raise FileNotFoundError("briefing not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    md_path = path.with_suffix(".md")
    payload["markdown"] = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    payload["stale"] = is_stale(payload.get("generated_at"), "briefing_daily")
    return payload


def get_context(market: str) -> dict[str, Any] | None:
    """1차 점수화용 최신 일간 브리핑 컨텍스트. stale이면 None (§4.7)."""
    market = require_storable_market(market)
    out_dir = _market_dir(market, "daily")
    if not out_dir.exists():
        return None
    files = sorted(out_dir.glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        payload = json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if is_stale(payload.get("generated_at"), "briefing_daily"):
        return None
    return payload


def _latest_summary(market: str) -> dict[str, Any] | None:
    ctx = get_context(market)
    if not ctx:
        return None
    return {"key": ctx.get("key"), "regime": ctx.get("regime", {}).get("regime"), "stale": False}
