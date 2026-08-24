from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.db import repository as _root


KST = timezone(timedelta(hours=9))


def save_strategy_lookup_result(
    run_id: str,
    strategy_id: str,
    result: dict,
    *,
    captured_at: str | None = None,
) -> None:
    _root.init_db()
    recorded_at = captured_at or datetime.now(KST).isoformat()
    candidates = list(result.get("candidates") or [])
    with _root.connect_db() as conn:
        conn.execute(
            """
            INSERT INTO strategy_lookup_runs (
                run_id, strategy_id, captured_at, scanned,
                candidate_count, min_score, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, strategy_id) DO UPDATE SET
                captured_at=excluded.captured_at,
                scanned=excluded.scanned,
                candidate_count=excluded.candidate_count,
                min_score=excluded.min_score,
                payload=excluded.payload
            """,
            (
                str(run_id),
                str(strategy_id),
                recorded_at,
                int(result.get("scanned") or 0),
                len(candidates),
                float(result.get("min_score") or 0),
                json.dumps(result, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()


def list_strategy_lookup_runs(limit: int = 30) -> list[dict]:
    _root.init_db()
    safe_limit = max(1, min(int(limit), 200))
    with _root.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT run_id,
                   MIN(captured_at) AS captured_at,
                   COUNT(*) AS strategy_count,
                   SUM(scanned) AS scanned,
                   SUM(candidate_count) AS candidate_count
            FROM strategy_lookup_runs
            GROUP BY run_id
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def load_strategy_lookup_run(run_id: str) -> list[dict]:
    _root.init_db()
    with _root.connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT run_id, strategy_id, captured_at, scanned,
                   candidate_count, min_score, payload
            FROM strategy_lookup_runs
            WHERE run_id=?
            ORDER BY strategy_id
            """,
            (str(run_id),),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["data"] = json.loads(item.pop("payload"))
        except (TypeError, ValueError, json.JSONDecodeError):
            item["data"] = {}
            item.pop("payload", None)
        results.append(item)
    return results
