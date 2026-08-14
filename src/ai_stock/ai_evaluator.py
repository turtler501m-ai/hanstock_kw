# -*- coding: utf-8 -*-
"""AI 평가기: 기존 ModelPredictor 래핑 (§5.11).

probability를 confidence로 '상승확률'처럼 노출하지 않는다. probability는 ai_score
산출 입력으로만 쓰고, AI 차단/저신뢰/오류 시 fallback 신호를 명확히 준다.
"""
from __future__ import annotations

from typing import Any


def _feature_quality(features: dict[str, Any]) -> float | None:
    if not isinstance(features, dict) or not features:
        return None
    required = ("rule_score", "technical_score", "momentum_score", "narrative_score", "risk_score")
    present = sum(1 for key in required if features.get(key) is not None)
    return present / len(required)


def _model_confidence(*, status: str, features: dict[str, Any], pred: dict[str, Any]) -> float | None:
    """Return model applicability confidence, not price-up probability."""
    if status != "ready":
        return None
    quality = _feature_quality(features)
    if quality is None:
        return None
    schema_ok = pred.get("ml_score") is not None
    cache_penalty = 0.05 if pred.get("cache_hit") else 0.0
    confidence = min(1.0, max(0.0, 0.45 + quality * 0.5 + (0.05 if schema_ok else 0.0) - cache_penalty))
    return round(confidence, 2)


def _profile_predictor(strategy_profile: dict[str, Any] | None):
    from src.strategy.predict import ModelPredictor

    return ModelPredictor(strategy_profile=strategy_profile or {})


def evaluate(features: dict[str, Any], *, strategy_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """단일 종목 features → AI 평가 결과.

    반환: ai_score(0~100), confidence(0~1|None), fallback_used, model_status, model.
    """
    result = {
        "ai_score": 0.0,
        "confidence": None,
        "fallback_used": False,
        "model_status": "disabled",
        "model": None,
        "reason": None,
    }
    try:
        predictor = _profile_predictor(strategy_profile)
    except Exception as exc:  # import/init 실패 → 룰 fallback
        result["fallback_used"] = True
        result["model_status"] = "fallback"
        result["reason"] = f"predictor init failed: {exc}"
        return result

    result["model"] = getattr(predictor, "model_name", None)
    try:
        pred = predictor.predict(dict(features))
    except Exception as exc:
        result["fallback_used"] = True
        result["model_status"] = "fallback"
        result["reason"] = str(exc)
        return result

    status = str(pred.get("model_status") or "disabled")
    result["model_status"] = status
    prob = pred.get("ml_score")
    if status in ("disabled",):
        result["fallback_used"] = False  # AI를 끈 상태(정상). 가중치 제외.
        result["reason"] = pred.get("fallback_reason")
        return result
    if status in ("fallback",) or prob is None:
        result["fallback_used"] = True
        result["reason"] = pred.get("fallback_reason")
        return result

    # probability(0~1) → ai_score(0~100). confidence는 별도 모델 적용 신뢰도(상승확률 아님).
    prob_f = max(0.0, min(1.0, float(prob)))
    result["ai_score"] = round(prob_f * 100.0, 1)
    result["confidence"] = _model_confidence(status=status, features=features, pred=pred)
    result["reason"] = pred.get("fallback_reason")
    # low_confidence는 scoring 단계에서 AI 가중치 0 처리(§5.11.2).
    return result
