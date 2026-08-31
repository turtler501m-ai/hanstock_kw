"""Audited alignment of the fill projection with authoritative broker balances."""

from __future__ import annotations

from datetime import datetime, timezone


def apply_latest_open_reconciliation_issues(connect, *, actor: str) -> dict:
    """Apply the newest broker snapshot per position and resolve its open issues.

    Quantity corrections are immutable ledger entries.  Cash flow is deliberately
    left unchanged because a balance snapshot does not provide trustworthy cost.
    """
    if not str(actor or "").strip():
        raise ValueError("actor is required")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    applied = []
    with connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        latest = conn.execute(
            """SELECT r.* FROM reconciliation_adjustments r
               JOIN (
                 SELECT account_key,market,symbol,MAX(id) AS id
                 FROM reconciliation_adjustments WHERE status='open'
                 GROUP BY account_key,market,symbol
               ) newest ON newest.id=r.id
               ORDER BY r.account_key,r.market,r.symbol"""
        ).fetchall()
        for raw in latest:
            issue = dict(raw)
            scope = (issue["account_key"], issue["market"], issue["symbol"])
            row = conn.execute(
                """SELECT quantity,net_cash_flow FROM positions
                   WHERE account_key=? AND market=? AND symbol=?""",
                scope,
            ).fetchone()
            current_qty = int(row[0]) if row else 0
            target_qty = int(issue["broker_qty"])
            delta = target_qty - current_qty
            conn.execute(
                """INSERT INTO position_quantity_adjustments
                   (account_key,market,symbol,quantity_delta,reconciliation_id,reason,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (*scope, delta, int(issue["id"]), "authoritative broker balance alignment", now),
            )
            conn.execute(
                """INSERT INTO positions(account_key,market,symbol,quantity,net_cash_flow,updated_at)
                   VALUES(?,?,?,?,0,?) ON CONFLICT(account_key,market,symbol) DO UPDATE SET
                   quantity=excluded.quantity,updated_at=excluded.updated_at""",
                (*scope, target_qty, now),
            )
            conn.execute(
                """UPDATE reconciliation_adjustments
                   SET status=CASE WHEN id=? THEN 'resolved' ELSE 'superseded' END,
                       reviewed_by=?,reviewed_at=?,
                       reason=reason || CASE WHEN id=?
                         THEN ' | applied to position projection'
                         ELSE ' | superseded by newer broker snapshot' END
                   WHERE account_key=? AND market=? AND symbol=? AND status='open'""",
                (int(issue["id"]), actor, now, int(issue["id"]), *scope),
            )
            applied.append({
                "issue_id": int(issue["id"]), "account_key": scope[0],
                "market": scope[1], "symbol": scope[2], "before_qty": current_qty,
                "broker_qty": target_qty, "quantity_delta": delta,
            })
    return {"applied_count": len(applied), "items": applied, "applied_at": now}
