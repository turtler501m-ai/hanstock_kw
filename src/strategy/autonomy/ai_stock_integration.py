"""Application wiring between AI-stock schedules and the autonomy platform."""
from __future__ import annotations

from src.db import ai_watchlist_repository as watchlist_repository
from src.db import ai_execution_repository as execution_repository
from src.db import ai_autonomy_repository as ai_stock_repository

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping

from src.approval_service import ApprovalService
from src.config import config

from src.db.repository import connect_db
from src.db.strategy_repository import load_ai_strategies
from src.repositories import ApprovalRepository

from .approval_bridge import ManagedApprovalBridge, ManagedApprovalPlanService
from .approval_bridge import ManagedExecutionCoordinator
from .broker_adapters import KRBrokerGateway, ManagedOrderReconciler
from .operational_context import OperationalSnapshotProvider
from .order_state import ManagedOrderService, OrderStatus
from .protection import HardStopProtectionService, PaperProtectionBroker
from .risk_envelope import RiskEnvelope, RiskSnapshot
from .runtime import (
    AutonomyRuntime,
    RuntimeConfigurationError,
    _risk_limits,
    build_runtime_contexts,
)


_DEMO_PROTECTION_BROKER = PaperProtectionBroker()


def _order_result_payload(result) -> dict[str, Any]:
    payload = dict(result.raw)
    payload.update({
        "accepted": bool(result.success),
        "broker_order_id": result.broker_order_id,
        "status": result.status.value,
        "message": result.message,
        "outcome_unknown": result.status.value == "unknown",
    })
    return payload


def _order_snapshot_payload(snapshot) -> dict[str, Any]:
    payload = dict(snapshot.raw)
    payload.update({
        "broker_order_id": snapshot.broker_order_id,
        "status": snapshot.status.value,
        "requested_qty": snapshot.requested_quantity,
        "cumulative_filled_qty": snapshot.filled_quantity,
        "remaining_qty": snapshot.remaining_quantity,
        "average_fill_price": snapshot.average_fill_price,
        "message": snapshot.message,
        "outcome_unknown": snapshot.outcome_unknown,
    })
    return payload


def _autonomy_execution_enabled() -> bool:
    """Allow demo, or real only after every explicit live opt-in is enabled."""
    environment = str(
        getattr(config, "autonomy_trading_env", "demo")
    ).lower()
    trading_environment = str(getattr(config, "trading_env", "demo")).lower()
    if environment == "demo" and trading_environment == "demo":
        return True
    return (
        environment == "real"
        and trading_environment == "real"
        and bool(getattr(config, "enable_live_trading", False))
        and bool(getattr(config, "autonomy_enable_live_trading", False))
        and bool(getattr(config, "autonomy_live_opt_in", False))
    )


class OperationalApprovalSnapshotProvider:
    """Rebuild trusted risk state immediately before a managed approval."""

    def __init__(self, snapshots: OperationalSnapshotProvider):
        self.snapshots = snapshots

    def snapshot_for_approval(
        self,
        *,
        order: Mapping[str, Any],
        decision: Mapping[str, Any],
        position: Mapping[str, Any],
        exclude_position_reservation_id: int | None,
    ) -> RiskSnapshot:
        market = str(order["market"])
        strategy_id = str(order["strategy_id"])
        current = self.snapshots.snapshot(market, strategy_id)
        _, portfolio = build_runtime_contexts(
            market=market,
            strategy_id=strategy_id,
            account_snapshot=current.account,
            market_snapshot=current.market,
            exclude_reservation_id=exclude_position_reservation_id,
        )
        risk = portfolio.risk_snapshot_for(
            str(order["symbol"]),
            position.get("id") if str(order.get("action")) == "sell" else None,
        )
        if risk is None:
            raise RuntimeConfigurationError(
                "fresh approval risk snapshot is unavailable"
            )
        return risk


def build_managed_approval_bridge(
    *,
    strategy_id: str,
    market: str,
    snapshots: OperationalSnapshotProvider | None = None,
    orders: ManagedOrderService | None = None,
) -> tuple[ManagedApprovalBridge, ManagedOrderService]:
    """Build the canonical approval bridge from current strategy configuration."""
    strategy = next(
        (
            item
            for item in load_ai_strategies()
            if str(item.get("id")) == str(strategy_id)
        ),
        None,
    )
    if not strategy:
        raise RuntimeConfigurationError("registered strategy is required")
    policy = ai_stock_repository.get_policy(strategy_id, market)
    profile = strategy.get("profile")
    risk = profile.get("risk") if isinstance(profile, Mapping) else None
    if not policy or not isinstance(profile, Mapping) or not isinstance(risk, Mapping):
        raise RuntimeConfigurationError(
            "strategy policy and complete risk profile are required"
        )
    limits = _risk_limits(policy, profile, risk)
    order_service = orders or ManagedOrderService(
        protection_broker=(
            _DEMO_PROTECTION_BROKER
            if _autonomy_execution_enabled()
            else None
        )
    )
    snapshot_provider = snapshots or OperationalSnapshotProvider(
        require_persisted_kr_regime=True
    )
    protection = HardStopProtectionService(repo=ai_stock_repository)
    bridge = ManagedApprovalBridge(
        ApprovalService(ApprovalRepository(connect_db)),
        order_service,
        risk_envelope=RiskEnvelope(limits),
        fresh_risk_snapshots=OperationalApprovalSnapshotProvider(snapshot_provider),
        protection=protection,
        repo=ai_stock_repository,
    )
    return bridge, order_service


def approve_managed_ai_stock_order(approval_id: int) -> dict[str, Any]:
    """Approve a managed AI-stock order without bypassing fresh risk checks."""
    approvals = ApprovalService(ApprovalRepository(connect_db))
    approval = approvals.get_pending_approval(int(approval_id))
    if not approval.managed_order_id:
        raise RuntimeConfigurationError("approval is not a managed AI-stock order")
    order = ai_stock_repository.get_managed_order(int(approval.managed_order_id))
    if not order:
        raise RuntimeConfigurationError("managed order is missing")
    bridge, orders = build_managed_approval_bridge(
        strategy_id=str(order["strategy_id"]),
        market=str(order["market"]),
    )
    approved = bridge.approve(int(approval_id))
    from src.application.orders.legacy_bridge import mirror_status
    from src.application.orders.repository import OrderLedgerRepository

    unified = OrderLedgerRepository(connect_db).get_by_approval(int(approval_id))
    mirror_status(
        connect_db, unified, "submitting" if str(approved["status"]) == "submitting" else "submitted"
        if str(approved["status"]) in {"submitted", "open"} else "approved",
        actor="managed_ai_stock", reason="managed order approval synchronized",
    )
    response = {
        "id": int(approval_id),
        "managed_order_id": int(order["id"]),
        "status": "approved",
        "order_status": str(approved["status"]),
        "response_msg": "managed AI-stock order approved after fresh risk validation",
    }
    if str(order["market"]) == "KR" and _autonomy_execution_enabled():
        from src.broker.factory import create_domestic_stock_broker

        api = create_domestic_stock_broker(order_submission_enabled=True)

        from src.broker.models import CancelOrderRequest, OrderRequest, OrderSide

        def submitter(canonical):
            result = api.submit_order(
                OrderRequest(
                    str(canonical["symbol"]),
                    OrderSide(str(canonical["action"])),
                    int(canonical["requested_qty"]),
                    int(float(canonical.get("requested_price") or 0)),
                )
            )
            return _order_result_payload(result)

        def canceler(canonical):
            result = api.submit_cancellation(CancelOrderRequest(
                str(canonical["broker_order_id"]), str(canonical["symbol"]), max(
                    0,
                    int(canonical["requested_qty"])
                    - int(canonical.get("filled_qty") or 0),
                ),
            ))
            return _order_result_payload(result)

        def query(canonical):
            created = str(canonical.get("created_at") or "")[:10].replace("-", "")
            return _order_snapshot_payload(api.fetch_order_snapshot(
                str(canonical["broker_order_id"]),
                order_date=created,
            ))

        gateway = KRBrokerGateway(
            submitter=submitter,
            canceler=canceler,
            query=query,
            repo=ai_stock_repository,
        )
        submitted = ManagedExecutionCoordinator(
            bridge.approvals,
            orders,
            pre_submit_validator=bridge.revalidate_for_execution,
            repo=ai_stock_repository,
        ).execute(int(approval_id), gateway)
        target = str(submitted["status"])
        target = "partial" if target == "partially_filled" else target
        unified = OrderLedgerRepository(connect_db).get_by_approval(int(approval_id))
        if target in {"submitted", "broker_unknown", "rejected", "failed"}:
            mirrored = mirror_status(
                connect_db, unified, target, actor="managed_ai_stock",
                reason="managed broker submission synchronized",
            )
            if mirrored and submitted.get("broker_order_id"):
                OrderLedgerRepository(connect_db).bind_broker_result(
                    int(mirrored["id"]), str(submitted["broker_order_id"]),
                    broker_order_date=str(created or "")[:10],
                )
        response.update(
            {
                "status": str(submitted["status"]),
                "order_status": str(submitted["status"]),
                "broker_order_id": submitted.get("broker_order_id"),
                "response_msg": (
                    "managed AI-stock order submitted to Kiwoom "
                    f"{getattr(config, 'trading_env', 'demo')}"
                ),
            }
        )
    return response


def reject_managed_ai_stock_order(
    approval_id: int, *, reason: str = "Rejected by dashboard"
) -> dict[str, Any]:
    """Reject both sides of a canonical managed approval."""
    approvals = ApprovalService(ApprovalRepository(connect_db))
    approval = approvals.get_pending_approval(int(approval_id))
    if not approval.managed_order_id:
        raise RuntimeConfigurationError("approval is not a managed AI-stock order")
    order = ai_stock_repository.get_managed_order(int(approval.managed_order_id))
    if not order:
        raise RuntimeConfigurationError("managed order is missing")
    bridge, _ = build_managed_approval_bridge(
        strategy_id=str(order["strategy_id"]),
        market=str(order["market"]),
    )
    rejected = bridge.reject(int(approval_id), reason=reason)
    return {
        "id": int(approval_id),
        "managed_order_id": int(order["id"]),
        "status": "rejected",
        "order_status": str(rejected["status"]),
    }


def cancel_managed_ai_stock_order(order_id: int) -> dict[str, Any]:
    """Cancel a queued or broker-submitted KR demo managed order."""
    order = ai_stock_repository.get_managed_order(int(order_id))
    if not order:
        raise RuntimeConfigurationError("managed order is missing")
    status = OrderStatus(str(order["status"]))
    orders = ManagedOrderService(
        repo=ai_stock_repository,
        protection_broker=_DEMO_PROTECTION_BROKER,
    )
    broker = None
    if status in {
        OrderStatus.SUBMITTING,
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.BROKER_UNKNOWN,
    }:
        if not (
            str(order.get("market")) == "KR"
            and _autonomy_execution_enabled()
        ):
            raise RuntimeConfigurationError(
                "broker cancellation requires an enabled KR autonomy environment"
            )
        from src.broker.factory import create_domestic_stock_broker

        api = create_domestic_stock_broker(order_submission_enabled=True)

        def unavailable_submit(_canonical):
            raise RuntimeConfigurationError("cancel gateway cannot submit orders")

        from src.broker.models import CancelOrderRequest

        def canceler(canonical):
            result = api.submit_cancellation(CancelOrderRequest(
                str(canonical["broker_order_id"]), str(canonical["symbol"]), max(
                    0,
                    int(canonical["requested_qty"])
                    - int(canonical.get("filled_qty") or 0),
                ),
            ))
            return _order_result_payload(result)

        def query(canonical):
            created = str(canonical.get("created_at") or "")[:10].replace("-", "")
            return _order_snapshot_payload(api.fetch_order_snapshot(
                str(canonical["broker_order_id"]), order_date=created
            ))

        broker = KRBrokerGateway(
            submitter=unavailable_submit,
            canceler=canceler,
            query=query,
            repo=ai_stock_repository,
        )
    canceled = orders.cancel(int(order_id), expected=status, broker=broker)
    return {
        "id": int(order_id),
        "status": str(canceled["status"]),
        "broker_order_id": canceled.get("broker_order_id"),
        "response_msg": "managed order cancellation processed",
    }


def run_ai_stock_autonomy_cycle(
    *,
    market: str,
    strategy_id: str,
    scan_id: int,
    run_type: str,
    snapshots: OperationalSnapshotProvider | None = None,
    runtime: AutonomyRuntime | None = None,
) -> dict[str, Any]:
    """Run one AI-stock autonomy cycle and queue every approved managed order."""
    approval_required = bool(
        getattr(config, "autonomy_require_approval", True)
    )
    if (
        not approval_required
        and (str(market).upper() != "KR" or not _autonomy_execution_enabled())
    ):
        raise RuntimeConfigurationError(
            "approval-free autonomy requires an enabled KR autonomy environment"
        )
    reconciliation = reconcile_kiwoom_managed_orders(market=market)
    policy = ai_stock_repository.get_policy(strategy_id, market)
    if not policy or not int(policy.get("enabled", 0)):
        raise RuntimeConfigurationError("enabled automation policy is required")
    snapshot_provider = snapshots or OperationalSnapshotProvider(
        require_persisted_kr_regime=True
    )
    engine = runtime or AutonomyRuntime()
    current = snapshot_provider.snapshot(market, strategy_id)
    cycle_key = (
        f"ai-stock:{run_type}:{market}:{strategy_id}:{scan_id}:"
        f"{datetime.now(timezone.utc).isoformat()}"
    )
    result = engine.run(
        cycle_key=cycle_key,
        strategy_id=strategy_id,
        market=market,
        account_snapshot=current.account,
        market_snapshot=current.market,
    )
    queued = ()
    if (
        int(policy.get("automation_level") or 0) >= 5
        and int(policy.get("auto_approve") or 0)
    ):
        bridge, orders = build_managed_approval_bridge(
            strategy_id=strategy_id,
            market=market,
            snapshots=snapshot_provider,
            orders=engine.order_service,
        )
        queued = ManagedApprovalPlanService(bridge, orders).queue_runtime_result(
            result
        )
    executions: list[dict[str, Any]] = []
    if not approval_required:
        for item in queued:
            executions.append(
                approve_managed_ai_stock_order(int(item.approval_id))
            )
    statuses: dict[str, int] = {}
    regime_rejections: list[str] = []
    for item in result.cycle.results:
        statuses[item.status] = statuses.get(item.status, 0) + 1
        for reason in getattr(item, "reasons", ()) or ():
            if reason in {"allowed_market_regime", "market_risk_multiplier_valid"}:
                regime_rejections.append(reason)
    from src.db.strategy_repository import load_ai_strategies
    from src.market_regime.policy import REGIME_RISK_CAPS, expand_allowed_regimes

    strategy = next(
        (row for row in load_ai_strategies() if str(row.get("id")) == str(strategy_id)),
        {},
    )
    profile = strategy.get("profile") if isinstance(strategy, dict) else {}
    allowed_regimes = expand_allowed_regimes(
        profile.get("market_regime_filter") if isinstance(profile, dict) else ()
    )
    current_regime = str(current.market.get("regime") or "")
    configured_caps = profile.get("market_regime_max_pct", {}) if isinstance(profile, dict) else {}
    try:
        configured_cap = max(0.0, min(1.0, float(configured_caps.get(current_regime, 100)) / 100.0))
    except (TypeError, ValueError):
        configured_cap = 0.0
    effective_multiplier = min(
        float(current.market.get("risk_multiplier", 1.0)),
        REGIME_RISK_CAPS.get(current_regime, 0.0),
        configured_cap,
    )
    regime_allowed = (
        current_regime in allowed_regimes
        and effective_multiplier > 0
        and not regime_rejections
    )
    regime_reason = (
        "market_regime_allowed" if regime_allowed
        else (regime_rejections[0] if regime_rejections else "allowed_market_regime")
    )
    return {
        "enabled": True,
        "cycle_key": result.cycle.cycle_key,
        "market_regime_policy": {
            "regime": current.market.get("regime"),
            "quality": current.market.get("regime_quality"),
            "multiplier": effective_multiplier,
            "source_multiplier": current.market.get("risk_multiplier", 1.0),
            "configured_max_pct": configured_cap * 100.0,
            "system_max_pct": REGIME_RISK_CAPS.get(current_regime, 0.0) * 100.0,
            "snapshot_id": current.market.get("snapshot_id"),
            "session_date": current.market.get("session_date"),
            "allowed": regime_allowed,
            "reason": regime_reason,
        },
        "scanned_intents": result.cycle.scanned_intents,
        "managed_positions": result.cycle.managed_positions,
        "result_counts": statuses,
        "managed_orders": [dict(item) for item in result.managed_orders],
        "approvals": [asdict(item) for item in queued],
        "executions": executions,
        "reconciliation": reconciliation,
    }


def reconcile_kiwoom_managed_orders(*, market: str = "KR") -> list[dict[str, Any]]:
    """Reconcile durable KR orders against the configured Kiwoom environment."""
    market = str(market).upper()
    if market != "KR" or not _autonomy_execution_enabled():
        return []
    unsettled = ai_stock_repository.list_unsettled_managed_orders(
        market="KR", limit=500
    )
    if not unsettled:
        return []
    from src.broker.factory import create_domestic_stock_broker

    api = create_domestic_stock_broker(order_submission_enabled=True)

    def unavailable_submit(_canonical):
        raise RuntimeConfigurationError("reconciliation cannot submit orders")

    from src.broker.models import CancelOrderRequest

    def canceler(canonical):
        result = api.submit_cancellation(CancelOrderRequest(
            str(canonical["broker_order_id"]), str(canonical["symbol"]), max(
                0,
                int(canonical["requested_qty"])
                - int(canonical.get("filled_qty") or 0),
            ),
        ))
        return _order_result_payload(result)

    def query(canonical):
        created = str(canonical.get("created_at") or "")[:10].replace("-", "")
        return _order_snapshot_payload(api.fetch_order_snapshot(
            str(canonical["broker_order_id"]), order_date=created
        ))

    broker = KRBrokerGateway(
        submitter=unavailable_submit,
        canceler=canceler,
        query=query,
        repo=ai_stock_repository,
    )
    service = ManagedOrderService(
        repo=ai_stock_repository,
        protection_broker=_DEMO_PROTECTION_BROKER,
    )
    results = ManagedOrderReconciler(
        service, broker, repo=ai_stock_repository
    ).recover_unsettled()
    if any(item.status in {"error", "inconsistent"} for item in results):
        raise RuntimeConfigurationError(
            "Kiwoom managed-order reconciliation is incomplete"
        )
    return [
        {
            "order_id": item.order_id,
            "before": item.before,
            "after": item.after,
            "applied_fill_qty": item.applied_fill_qty,
            "status": item.status,
            "reason": item.reason,
        }
        for item in results
    ]
