"""SQLite database for futures signals."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import os

KST = timezone(timedelta(hours=9))


def get_db_path() -> Path:
    return Path(os.environ.get("SIGNALS_DB_PATH", ".runtime/signals.db"))


def init_db():
    """Initialize the signals database."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_key TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                message_date TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                symbol TEXT,
                direction TEXT,
                entry_price REAL,
                stop_loss REAL,
                target_price REAL,
                confidence REAL,
                notes TEXT,
                UNIQUE(channel_key, message_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS poll_state (
                channel_key TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                last_polled_at TEXT
            )
        """)


@contextmanager
def get_conn():
    """Get a database connection."""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_channel_state(channel_key: str) -> dict[str, Any] | None:
    """Get the poll state for a channel."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_message_id, last_polled_at FROM poll_state WHERE channel_key = ?",
            (channel_key,),
        ).fetchone()
        if row:
            return {"last_message_id": row["last_message_id"], "last_polled_at": row["last_polled_at"]}
        return None


def update_channel_state(channel_key: str, last_message_id: int):
    """Update the poll state for a channel."""
    polled_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO poll_state (channel_key, last_message_id, last_polled_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_key) DO UPDATE SET
                last_message_id = excluded.last_message_id,
                last_polled_at = excluded.last_polled_at
        """, (channel_key, last_message_id, polled_at))


def insert_signal(
    channel_key: str,
    message_id: int,
    message_date: str,
    raw_text: str,
    symbol: str | None,
    direction: str | None,
    entry_price: float | None,
    stop_loss: float | None,
    target_price: float | None,
    confidence: float | None,
    notes: str | None,
) -> bool:
    """Insert a new signal. Returns True if inserted, False if duplicate."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        try:
            cur = conn.execute("""
                INSERT INTO signals
                (channel_key, message_id, message_date, fetched_at, raw_text,
                 symbol, direction, entry_price, stop_loss, target_price,
                 confidence, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                channel_key,
                message_id,
                message_date,
                fetched_at,
                raw_text,
                symbol,
                direction,
                entry_price,
                stop_loss,
                target_price,
                confidence,
                notes,
            ))
            return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False


def list_signals(
    channel_key: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List signals from the database."""
    with get_conn() as conn:
        if channel_key:
            rows = conn.execute("""
                SELECT * FROM signals
                WHERE channel_key = ?
                ORDER BY message_date DESC
                LIMIT ?
            """, (channel_key, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM signals
                ORDER BY message_date DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]