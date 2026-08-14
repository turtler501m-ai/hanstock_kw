# -*- coding: utf-8 -*-
"""탭2 내러티브: 기존 내러티브 모멘텀을 복제하지 않고 직접 재사용 (§5.2·§2.4).

KR 내러티브를 기본 제공하고, US는 소스가 준비되지 않으면 허위 생성하지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.ai_stock.constants import MARKET_KR, MARKET_US
from src.ai_stock.markets import markets_for_query

BASE_DIR = Path(__file__).resolve().parents[2]
NARRATIVE_HISTORY_PATH = BASE_DIR / ".runtime" / "narrative_history.json"
THEME_MAP_PATH = BASE_DIR / "config" / "theme_map.json"


def _kr_narratives() -> dict[str, Any]:
    try:
        from src.strategy import narrative_momentum_runner as runner
        from src.strategy.narrative_momentum import NarrativeMomentumStrategy

        history, theme_map, errors = runner.load_inputs(NARRATIVE_HISTORY_PATH, THEME_MAP_PATH)
        strategy = NarrativeMomentumStrategy()
        status = strategy.status(history, theme_map)
        signals = strategy.calculate_signals(history, theme_map)
        return {
            "market": MARKET_KR,
            "supported": True,
            "status": status,
            "signals": signals,
            "count": len(signals),
            "errors": errors,
        }
    except Exception as exc:  # 소스/데이터 문제 → 빈 결과(허위 생성 금지)
        return {"market": MARKET_KR, "supported": True, "signals": [], "count": 0, "errors": [str(exc)]}


def _us_narratives() -> dict[str, Any]:
    # US 내러티브 소스 미검증 → 활성화 전까지 미지원으로 명시(§5.2).
    return {"market": MARKET_US, "supported": False, "signals": [], "count": 0,
            "note": "US 내러티브 소스 준비 후 활성화"}


def list_narratives(market: str) -> dict[str, Any]:
    out = []
    for m in markets_for_query(market):
        out.append(_kr_narratives() if m == MARKET_KR else _us_narratives())
    return {"narratives": out}


def narrative_score_for(market: str, symbol: str, name: str | None = None) -> dict[str, Any]:
    """종목의 내러티브 점수(0~100)와 연결 근거. 매칭 없으면 0."""
    if market != MARKET_KR:
        return {"narrative_score": 0.0, "related": [], "evidence": []}
    data = _kr_narratives()
    for sig in data.get("signals", []):
        if str(sig.get("ticker")) == str(symbol):
            return {
                "narrative_score": float(sig.get("final_score") or 0.0),
                "related": list(sig.get("narratives", []))[:5],
                "evidence": sig.get("evidence", []),
            }
    return {"narrative_score": 0.0, "related": [], "evidence": []}
