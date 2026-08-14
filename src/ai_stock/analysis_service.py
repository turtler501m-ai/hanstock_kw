# -*- coding: utf-8 -*-
"""탭4 종목 분석 (§5.4).

탭3이 저장한 후보 결과를 재사용해 상세 근거를 집계한다(점수를 재계산하지 않음).
"""
from __future__ import annotations

from typing import Any


def enrich(candidate: dict[str, Any]) -> dict[str, Any]:
    c = dict(candidate)
    c["analysis"] = {
        "summary": {
            "market": c.get("market"),
            "symbol": c.get("symbol"),
            "current_price": c.get("current_price"),
            "final_score": c.get("final_score"),
            "decision": c.get("decision"),
            "market_regime": c.get("market_regime"),
            "fallback_used": c.get("fallback_used"),
            "data_as_of": c.get("data_as_of"),
        },
        "scores": {
            "rule": c.get("rule_score"), "technical": c.get("technical_score"),
            "momentum": c.get("momentum_score"), "narrative": c.get("narrative_score"),
            "ai": c.get("ai_score"), "risk": c.get("risk_score"),
        },
        "narrative": {"related": c.get("related_narratives", [])},
        "ai_opinion": {
            "positive_factors": c.get("positive_factors", []),
            "negative_factors": c.get("negative_factors", []),
            "warnings": c.get("warnings", []),
            "invalidation_conditions": c.get("invalidation_conditions", []),
            "confidence_note": "confidence는 모델 신뢰 수준이며 상승확률이 아님",
        },
        "disclaimer": "AI는 미래 목표가격·수익률을 확정 제시하지 않는다(§5.4).",
    }
    return c
