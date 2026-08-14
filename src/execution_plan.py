from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class PlanRow:
    symbol: str
    name: str
    action: str
    qty: int
    price: int
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
        qty=int(signal.get("qty", 0) or 0),
        price=int(signal.get("price", 0) or 0),
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

    row = PlanRow(
        symbol=str(order.get("ticker", candidate.get("ticker", ""))),
        name=str(candidate.get("name") or order.get("ticker", candidate.get("ticker", ""))),
        action="buy",
        qty=int(order.get("quantity", 0) or 0),
        price=int(order.get("limit_price", 0) or 0),
        reason=reason,
        source=source,
        category="candidate",
        score=score,
        reasons=reasons,
        estimated_cost=float(order.get("estimated_cost", 0) or 0),
        metadata=dict(metadata or {}),
        strategy_id=strategy_id,
    )
    return row.to_dict()


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
