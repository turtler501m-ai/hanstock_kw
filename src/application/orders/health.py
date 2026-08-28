from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db.migrations import MIGRATIONS


def build_order_health(connect, *, stale_minutes: int = 10, include_runtime: bool = True) -> dict:
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")
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
                     )"""
            ).fetchone()[0])
        except Exception:
            legacy_unmirrored_count = 0
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
    computed_state = "reduce_only" if blockers else "ready"
    runtime = None
    if include_runtime:
        from src.application.orders.recovery import get_runtime_state
        runtime = get_runtime_state(connect)
    state = runtime["state"] if runtime and runtime["state"] != "ready" else computed_state
    if runtime and runtime["state"] == "ready" and blockers:
        state = "reduce_only"
    return {
        "state": state,
        "new_risk_allowed": state == "ready" and not blockers,
        "blockers": blockers,
        "warnings": [],
        "orders_by_status": {str(row[0]): int(row[1]) for row in status_rows},
        "schema": {
            "ready": schema_ready,
            "expected_version": max(expected_migrations, default=0),
            "applied_version": max(applied_migrations, default=0),
        },
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime": runtime,
    }


class NewRiskBlockedError(RuntimeError):
    def __init__(self, blockers: list[dict]):
        self.blockers = blockers
        codes = ", ".join(str(item.get("code")) for item in blockers)
        super().__init__(f"new risk is blocked until recovery completes: {codes}")


def assert_new_risk_allowed(connect) -> None:
    health = build_order_health(connect)
    runtime = health.get("runtime") or {}
    if health["state"] == "recovering" and runtime.get("updated_at") is None:
        # CLI/test workers may execute without the FastAPI lifespan. Perform
        # the same persisted-invariant recovery lazily before deciding.
        from src.application.orders.recovery import run_startup_recovery
        run_startup_recovery(connect)
        health = build_order_health(connect)
    if health["state"] == "recovering" and not health["blockers"]:
        health["blockers"].append({"code": "RUNTIME_RECOVERING", "count": 1})
    if not health["new_risk_allowed"]:
        raise NewRiskBlockedError(health["blockers"])
