from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ExecutionContext:
    dry_run: bool
    trading_env: str
    enable_live_trading: bool
    require_approval: bool
    online_access_blocked: bool = False
    analysis_only: bool = False


@dataclass(frozen=True)
class ExecutionDecision:
    decision: str
    reason: str


@dataclass(frozen=True)
class ExecutionResult:
    decision: str
    ok: bool
    response_msg: str
    broker_result: dict | None = None
    approval_id: int | None = None


def resolve_execution_decision(
    context: ExecutionContext,
    *,
    allow_approval_bypass: bool = False,
) -> ExecutionDecision:
    if context.online_access_blocked:
        return ExecutionDecision("reject", "online access is blocked")

    import os
    if os.environ.get("HANSTOCK_SCHEDULE_FORCE") == "1" or os.environ.get("MISTOCK_SCHEDULE_FORCE") == "1":
        return ExecutionDecision("execute", "execution allowed by forced testing bypass")

    if context.analysis_only:
        return ExecutionDecision("queue", "analysis_only mode")
    if context.require_approval and not allow_approval_bypass:
        return ExecutionDecision("queue", "approval required")
    if context.dry_run:
        return ExecutionDecision("execute", "dry_run simulation")
    if context.trading_env == "real" and not context.enable_live_trading:
        return ExecutionDecision("reject", "live trading switch disabled")
    return ExecutionDecision("execute", "execution allowed")


def submit_order_request(
    *,
    context: ExecutionContext,
    symbol: str,
    name: str,
    action: str,
    qty: int,
    price: int,
    reason: str,
    source: str,
    execute_order_fn: Callable[[str, str, int, int], dict],
    save_trade_fn: Callable[[str, str, str, int, int, str, bool], None],
    queue_order_fn: Callable[[str, str, str, int, int, str, str], int] | None = None,
    allow_approval_bypass: bool = False,
) -> ExecutionResult:
    if not str(symbol or "").strip():
        return ExecutionResult("reject", False, "symbol is required")
    if action not in {"buy", "sell"}:
        return ExecutionResult("reject", False, "action must be buy or sell")
    if qty <= 0:
        return ExecutionResult("reject", False, "qty must be greater than 0")
    if price < 0:
        return ExecutionResult("reject", False, "price must be greater than or equal to 0")

    policy = resolve_execution_decision(context, allow_approval_bypass=allow_approval_bypass)
    if policy.decision == "queue":
        if queue_order_fn is None:
            return ExecutionResult("reject", False, "approval queue unavailable")
        approval_id = queue_order_fn(symbol, name, action, qty, price, reason, source)
        return ExecutionResult("queue", True, policy.reason, approval_id=approval_id)
    if policy.decision == "reject":
        return ExecutionResult("reject", False, policy.reason)

    broker_result = execute_order_fn(symbol, action, price, qty)
    ok = broker_result.get("rt_cd") == "0"
    response_msg = str(broker_result.get("msg1", policy.reason))
    save_trade_fn(symbol, name, action, qty, price, reason, ok)
    return ExecutionResult(
        "execute",
        ok,
        response_msg,
        broker_result=broker_result,
    )
