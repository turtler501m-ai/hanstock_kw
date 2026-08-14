# -*- coding: utf-8 -*-
"""결정적 점수 산식 (§4.4·§5.11.2).

base_score = rule*w + technical*w + momentum*w + narrative*w + ai*w
final_score = clamp(base * regime_mult * data_quality_mult - risk_penalty, 0, 100)

가중치는 전략 profile에서 변경 가능(§1.4). AI confidence 미달/fallback 시
AI 가중치 0 후 나머지를 재정규화한다(룰 미달 후보는 승격 불가).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ai_stock.constants import (
    DECISION_AVOID,
    DECISION_INSUFFICIENT,
    DECISION_NEUTRAL,
    DECISION_STRONG_WATCH,
    DECISION_WATCH,
)

DEFAULT_WEIGHTS = {
    "rule": 0.20,
    "technical": 0.20,
    "momentum": 0.15,
    "narrative": 0.25,
    "ai": 0.20,
}

# decision 임계 (final_score 기준). 설정 가능(§1.4).
DEFAULT_DECISION_BANDS = {
    DECISION_STRONG_WATCH: 80.0,
    DECISION_WATCH: 65.0,
    DECISION_NEUTRAL: 45.0,
    DECISION_AVOID: 0.0,
}

# 룰 최소 점수 미달 후보는 watch 이상으로 승격 불가(§4.4).
DEFAULT_MIN_RULE_SCORE = 40.0
DEFAULT_MIN_AI_CONFIDENCE = 0.60


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


@dataclass
class ScoreProfile:
    """전략 profile에서 주입되는 점수 설정(§1.4 설정 가능성)."""

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    decision_bands: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_DECISION_BANDS))
    min_rule_score: float = DEFAULT_MIN_RULE_SCORE
    min_ai_confidence: float = DEFAULT_MIN_AI_CONFIDENCE

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ScoreProfile":
        raw = raw or {}
        p = cls()
        w = raw.get("weights")
        if isinstance(w, dict):
            p.weights = {k: float(w.get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS}
        b = raw.get("decision_bands")
        if isinstance(b, dict):
            p.decision_bands.update({k: float(v) for k, v in b.items()})
        if "min_rule_score" in raw:
            p.min_rule_score = float(raw["min_rule_score"])
        if "min_ai_confidence" in raw:
            p.min_ai_confidence = float(raw["min_ai_confidence"])
        return p


def _normalized_weights(weights: dict[str, float], *, drop_ai: bool) -> dict[str, float]:
    w = {k: max(0.0, float(weights.get(k, 0.0))) for k in DEFAULT_WEIGHTS}
    if drop_ai:
        w["ai"] = 0.0
    total = sum(w.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in w.items()}


def compute_base_score(components: dict[str, float], weights: dict[str, float]) -> float:
    return sum(clamp(components.get(k, 0.0)) * weights.get(k, 0.0) for k in DEFAULT_WEIGHTS)


def compute_final_score(
    *,
    base_score: float,
    regime_multiplier: float = 1.0,
    data_quality_multiplier: float = 1.0,
    risk_penalty: float = 0.0,
) -> float:
    raw = base_score * float(regime_multiplier) * float(data_quality_multiplier) - max(0.0, float(risk_penalty))
    return clamp(raw)


def decide(final_score: float, rule_score: float, profile: ScoreProfile, *, insufficient: bool = False) -> str:
    """결정적 판단 (§4.4). 룰 미달이면 watch 이상 불가."""
    if insufficient:
        return DECISION_INSUFFICIENT
    bands = profile.decision_bands
    if final_score >= bands.get(DECISION_STRONG_WATCH, 80.0):
        decision = DECISION_STRONG_WATCH
    elif final_score >= bands.get(DECISION_WATCH, 65.0):
        decision = DECISION_WATCH
    elif final_score >= bands.get(DECISION_NEUTRAL, 45.0):
        decision = DECISION_NEUTRAL
    else:
        decision = DECISION_AVOID
    # 룰 최소 점수 미달 후보는 watch 이상으로 승격 불가
    if rule_score < profile.min_rule_score and decision in (DECISION_STRONG_WATCH, DECISION_WATCH):
        decision = DECISION_NEUTRAL
    return decision


def score_candidate(
    *,
    components: dict[str, float],
    profile: ScoreProfile,
    regime_multiplier: float = 1.0,
    data_quality_multiplier: float = 1.0,
    risk_penalty: float = 0.0,
    ai_confidence: float | None = None,
    ai_fallback: bool = False,
    ai_disabled: bool = False,
    insufficient: bool = False,
) -> dict[str, Any]:
    """후보 1건의 최종 점수와 판단을 결정적으로 산출.

    - ai_confidence 미달·fallback·disabled이면 AI 가중치를 0으로 두고 재정규화.
    - 모든 구성 점수는 0~100으로 clamp.
    """
    drop_ai = (
        ai_fallback
        or ai_disabled
        or (ai_confidence is not None and ai_confidence < profile.min_ai_confidence)
    )
    weights = _normalized_weights(profile.weights, drop_ai=drop_ai)
    comp = {k: clamp(components.get(k, 0.0)) for k in DEFAULT_WEIGHTS}
    rule_score = comp["rule"]
    base = compute_base_score(comp, weights)
    final = compute_final_score(
        base_score=base,
        regime_multiplier=regime_multiplier,
        data_quality_multiplier=data_quality_multiplier,
        risk_penalty=risk_penalty,
    )
    decision = decide(final, rule_score, profile, insufficient=insufficient)
    return {
        "rule_score": round(comp["rule"], 1),
        "technical_score": round(comp["technical"], 1),
        "momentum_score": round(comp["momentum"], 1),
        "narrative_score": round(comp["narrative"], 1),
        "ai_score": round(comp["ai"], 1),
        "risk_score": round(clamp(risk_penalty), 1),
        "final_score": round(final, 1),
        "confidence": round(float(ai_confidence), 2) if ai_confidence is not None else None,
        "decision": decision,
        "ai_weight_applied": round(weights["ai"], 3),
        "fallback_used": bool(ai_fallback),
    }
