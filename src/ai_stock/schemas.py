# -*- coding: utf-8 -*-
"""공통 API 응답 envelope와 JSON 검증 (§7.1·F01·F06).

모든 AI스톡 API는 시장과 무관하게 동일 envelope를 반환한다.
"""
from __future__ import annotations

import json
from typing import Any

from src.ai_stock.safety import safety_state


def envelope(
    data: Any,
    *,
    market: str = "ALL",
    meta: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    ok: bool | None = None,
) -> dict[str, Any]:
    """표준 응답 (§7.1)."""
    err = list(errors or [])
    base_meta = {
        "data_as_of": None,
        "data_quality": "good",
        "stale": False,
        "fallback_used": False,
    }
    if meta:
        base_meta.update(meta)
    return {
        "ok": (len(err) == 0) if ok is None else bool(ok),
        "market": market,
        "data": data,
        "meta": base_meta,
        "safety": safety_state(),
        "errors": err,
    }


def loads_json(value: Any, default: Any) -> Any:
    """저장된 JSON 문자열을 파싱(검증 후 응답에 사용, §6.2)."""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, default=str)
