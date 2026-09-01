from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db.migrations import MIGRATIONS


def build_order_health(connect, *, stale_minutes: int = 10, include_runtime: bool = True) -> dict:
    checked_at = datetime.now(timezone.utc)
    threshold = (checked_at - timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")
    kst = timezone(timedelta(hours=9))
    legacy_threshold = (checked_at.astimezone(kst) - timedelta(minutes=stale_minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    legacy_now = checked_at.astimezone(kst).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) FROM orders GROUP BY status ORDER BY status"
        ).fetchall()
        unknown_count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='broker_unknown'"
        ).fetchone()[0]
        stale_count = conn.execute(
            """SELECT COUNT(*) FROM orders
               WHERE status IN ('submitting','submitted','open','partial','cancel_pending')
                 AND updated_at < ?""",
            (threshold,),
        ).fetchone()[0]
        reconciliation_count = conn.execute(
            "SELECT COUNT(*) FROM reconciliation_adjustments WHERE status='open'"
        ).fetchone()[0]
        migration_rows = conn.execute(
            "SELECT version,checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        try:
            unprotected_count = int(conn.execute(
                """SELECT COUNT(*) FROM ai_position_protections
                   WHERE status IN ('unprotected','failed','unknown')"""
            ).fetchone()[0])
        except Exception:
            unprotected_count = 0
        try:
            legacy_unmirrored_count = int(conn.execute(
                """SELECT COUNT(*) FROM trades t
                   WHERE t.order_status IN ('submitted','open','partial','broker_unknown')
                     AND NOT EXISTS (
                       SELECT 1 FROM orders o
                     WHERE json_extract(o.metadata_json,'$.legacy_trade_id')=t.id
                          OR (t.source_approval_id IS NOT NULL
                              AND o.approval_id=t.source_approval_id)
                          OR (COALESCE(t.broker_order_id,'')<>''
                              AND o.broker_order_id=t.broker_order_id
                              AND o.broker_order_date=substr(t.ts,1,10)
                              AND o.market='KR')
                     )"""
            ).fetchone()[0])
        except Exception:
            legacy_unmirrored_count = 0
        # Legacy approvals remain operator-visible even though unified orders are
        # the execution ledger. Surface backlog here so a nominally ready engine
        # cannot hide approvals that will never be acted on.
        approval_counts = {"pending": 0, "stale": 0, "expired": 0}
        approval_queries = {
            "pending": ("SELECT COUNT(*) FROM approvals WHERE status='pending'", ()),
            "stale": (
                "SELECT COUNT(*) FROM approvals WHERE status='pending' AND updated_at < ?",
                (legacy_threshold,),
            ),
            "expired": (
                """SELECT COUNT(*) FROM approvals
                   WHERE status='pending' AND expires_at IS NOT NULL
                     AND expires_at <> '' AND expires_at < ?""",
                (legacy_now,),
            ),
        }
        for key, (query, params) in approval_queries.items():
            try:
                approval_counts[key] = int(conn.execute(query, params).fetchone()[0])
            except Exception:
                # Older installations may not have every optional approval
                # column yet. Preserve the diagnostics that are available.
                pass
        pending_approval_count = approval_counts["pending"]
        stale_pending_approval_count = approval_counts["stale"]
        expired_pending_approval_count = approval_counts["expired"]
    expected_migrations = {item.version: item.checksum for item in MIGRATIONS}
    applied_migrations = {int(row[0]): str(row[1]) for row in migration_rows}
    schema_ready = applied_migrations == expected_migrations
    blockers = []
    if unknown_count:
        blockers.append({"code": "BROKER_UNKNOWN", "count": unknown_count})
    if stale_count:
        blockers.append({"code": "STALE_ACTIVE_ORDER", "count": stale_count})
    if reconciliation_count:
        blockers.append({"code": "RECONCILIATION_OPEN", "count": reconciliation_count})
    if unprotected_count:
        blockers.append({"code": "UNPROTECTED_POSITION", "count": unprotected_count})
    if legacy_unmirrored_count:
        blockers.append({"code": "LEGACY_ACTIVE_ORDER_UNMIRRORED", "count": legacy_unmirrored_count})
    if Path(".runtime/kill_switch.json").exists():
        blockers.append({"code": "KILL_SWITCH_ACTIVE", "count": 1})
    if not schema_ready:
        blockers.append({"code": "SCHEMA_NOT_READY", "count": 1})
    warnings = []
    if stale_pending_approval_count:
        warnings.append({"code": "STALE_PENDING_APPROVAL", "count": stale_pending_approval_count})
    if expired_pending_approval_count:
        warnings.append({"code": "EXPIRED_PENDING_APPROVAL", "count": expired_pending_approval_count})
    computed_state = "reduce_only" if blockers else "ready"
    runtime = None
    if include_runtime:
        from src.application.orders.recovery import get_runtime_state
        runtime = get_runtime_state(connect)
    state = runtime["state"] if runtime and runtime["state"] != "ready" else computed_state
    if runtime and runtime["state"] == "ready" and blockers:
        state = "reduce_only"
    if runtime and runtime["state"] != computed_state:
        warnings.append({
            "code": "RUNTIME_STATE_MISMATCH", "count": 1,
            "runtime_state": runtime["state"], "computed_state": computed_state,
        })
    operational_status = "degraded" if blockers or warnings or state != "ready" else "healthy"
    return {
        "state": state,
        "operational_status": operational_status,
        # Recovery findings remain visible diagnostics, but no longer impose a
        # blanket buy block. Order-specific risk controls still run separately.
        "new_risk_allowed": True,
        "blockers": blockers,
        "warnings": warnings,
        "approvals": {
            "pending": pending_approval_count,
            "stale_pending": stale_pending_approval_count,
            "expired_pending": expired_pending_approval_count,
        },
        "orders_by_status": {str(row[0]): int(row[1]) for row in status_rows},
        "schema": {
            "ready": schema_ready,
            "expected_version": max(expected_migrations, default=0),
            "applied_version": max(applied_migrations, default=0),
        },
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "runtime": runtime,
    }


class NewRiskBlockedError(RuntimeError):
    def __init__(self, blockers: list[dict]):
        self.blockers = blockers
        codes = ", ".join(str(item.get("code")) for item in blockers)
        super().__init__(f"new risk is blocked until recovery completes: {codes}")


def assert_new_risk_allowed(connect) -> None:
    """Compatibility hook; recovery diagnostics no longer block new buys."""
    return None
