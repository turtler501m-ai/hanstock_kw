from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class PlanRow:
    symbol: str
    name: str
    action: str
    qty: int | float
    price: int | float
    reason: str
    source: str
    category: str
    ok: bool | None = None
    decision: str | None = None
    indicators: dict[str, Any] = field(default_factory=dict)
    score: int | float | None = None
    reasons: list[str] = field(default_factory=list)
    estimated_cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    strategy_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _plan_number(value: Any) -> int | float:
    """Preserve fractional US prices while keeping whole KR values compact."""
    number = float(value or 0)
    return int(number) if number.is_integer() else number


def signal_to_plan_row(
    symbol: str,
    name: str,
    signal: dict[str, Any],
    *,
    source: str = "holding_signal",
    include_hold: bool = False,
    metadata: dict[str, Any] | None = None,
    strategy_id: str | None = None,
) -> dict[str, Any] | None:
    action = str(signal.get("action", "hold"))
    if action == "hold" and not include_hold:
        return None

    row = PlanRow(
        symbol=symbol,
        name=name,
        action=action,
        qty=_plan_number(signal.get("qty", 0)),
        price=_plan_number(signal.get("price", 0)),
        reason=str(signal.get("reason", "")),
        source=source,
        category="position",
        indicators=dict(signal.get("indicators") or {}),
        metadata=dict(metadata or {}),
        strategy_id=strategy_id,
    )
    return row.to_dict()


def candidate_order_to_plan_row(
    candidate: dict[str, Any],
    order: dict[str, Any],
    *,
    source: str = "candidate_order",
    metadata: dict[str, Any] | None = None,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    score = order.get("score", candidate.get("score"))
    reasons = list(order.get("reasons") or candidate.get("reasons") or [])
    reason = f"new buy score={score} ({', '.join(reasons)})" if reasons else f"new buy score={score}"

    rejection = dict(candidate.get("order_rejection") or {})
    row = PlanRow(
        symbol=str(order.get("ticker", candidate.get("ticker", ""))),
        name=str(candidate.get("name") or order.get("ticker", candidate.get("ticker", ""))),
        action="buy",
        qty=_plan_number(order.get("quantity", 0)),
        price=_plan_number(order.get("limit_price") or candidate.get("limit_price", 0)),
        reason=reason,
        source=source,
        category="candidate",
        score=score,
        reasons=reasons,
        estimated_cost=float(order.get("estimated_cost", 0) or 0),
        metadata={
            **dict(metadata or {}),
            **({"order_rejection": rejection} if rejection else {}),
        },
        strategy_id=strategy_id,
    )
    result = row.to_dict()
    if rejection:
        result["skip_reason"] = str(rejection.get("reason") or rejection.get("code") or "order rejected")
    return result


def build_execution_plan(
    *,
    position_rows: Iterable[dict[str, Any] | None] = (),
    candidate_rows: Iterable[dict[str, Any] | None] = (),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in position_rows:
        if row is not None:
            rows.append(row)
    for row in candidate_rows:
        if row is not None:
            rows.append(row)
    return rows
