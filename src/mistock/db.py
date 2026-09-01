from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.db.connection import open_sqlite
from src.mistock.config import config

KST = timezone(timedelta(hours=9))

DEFAULT_WATCHLIST = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("AMZN", "Amazon"),
    ("META", "Meta Platforms"),
    ("GOOGL", "Alphabet"),
    ("TSLA", "Tesla"),
    ("AVGO", "Broadcom"),
    ("AMD", "Advanced Micro Devices"),
    ("NFLX", "Netflix"),
]


def now_text() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def connect_db() -> sqlite3.Connection:
    return open_sqlite(config.trade_db_path, row_factory=sqlite3.Row)


def init_db() -> None:
    conn = connect_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS holdings (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                qty REAL NOT NULL,
                avg_price REAL NOT NULL,
                is_managed INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                reason TEXT,
                ok INTEGER NOT NULL,
                env TEXT,
                dry_run INTEGER,
                order_status TEXT,
                response_msg TEXT,
                broker_result TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                reason TEXT,
                source TEXT,
                status TEXT NOT NULL,
                response_msg TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS managed_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_key TEXT NOT NULL UNIQUE,
                broker_order_no TEXT,
                approval_id INTEGER,
                strategy_id TEXT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                requested_qty REAL NOT NULL,
                requested_price REAL NOT NULL,
                filled_qty REAL NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                status TEXT NOT NULL,
                last_error TEXT,
                broker_payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mistock_managed_orders_status ON managed_orders(status, updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mistock_managed_orders_broker_no ON managed_orders(broker_order_no)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mistock_managed_orders_approval ON managed_orders(approval_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scanned_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                score REAL NOT NULL,
                reasons TEXT,
                price REAL,
                env TEXT NOT NULL,
                rsi REAL,
                rsi2 REAL,
                macd_hist REAL,
                sma20 REAL,
                sma60 REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS performance_cashflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                synced_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                message TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                weight REAL NOT NULL,
                description TEXT,
                selected INTEGER NOT NULL DEFAULT 0,
                status TEXT DEFAULT 'approved',
                profile_json TEXT,
                strategy_version INTEGER DEFAULT 1,
                profile_hash TEXT,
                last_verified_at TEXT,
                last_backtested_at TEXT,
                last_paper_started_at TEXT,
                last_paper_completed_at TEXT,
                last_used_at TEXT,
                last_validation_result TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_strategy_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version INTEGER,
                event_type TEXT NOT NULL,
                payload TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_schedules (
                strategy_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                start_hm TEXT NOT NULL DEFAULT '2100',
                end_hm TEXT NOT NULL DEFAULT '0600',
                weekdays TEXT NOT NULL DEFAULT '1-5/2-6',
                mode TEXT NOT NULL DEFAULT 'execute',
                auto_approve INTEGER NOT NULL DEFAULT 0,
                last_run_at TEXT
            )
            """
        )
        for symbol, name in DEFAULT_WATCHLIST:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, name, created_at) VALUES (?, ?, ?)",
                (symbol, name, now_text()),
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO settings (key, value)
            VALUES ('cash', ?), ('ai_auto_add', 'false'), ('ai_auto_add_threshold', '3')
            """,
            (str(config.total_capital),),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO ai_strategies (
                id, name, provider, model, weight, description, selected, status, profile_json,
                strategy_version, profile_hash, last_verified_at, last_backtested_at, last_validation_result
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 'approved', ?, 1, 'mistock-default-v1', ?, ?, ?)
            """,
            (
                "mistock_nasdaq_rule_v1",
                "Mistock NASDAQ Rule Strategy",
                "none",
                "rule_based",
                0.0,
                "NASDAQ demo strategy cloned from Hanstock workflow with yfinance market data.",
                json.dumps({"market": "NASDAQ", "universe": "NASDAQ100", "ai_weight": 0.0}, ensure_ascii=False),
                now_text(),
                now_text(),
                json.dumps({"checks": {"static": {"ok": True, "status": "passed"}}}, ensure_ascii=False),
            ),
        )
        # Migrations for fee and tax tracking
        try:
            conn.execute("ALTER TABLE holdings ADD COLUMN is_managed INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        for col_name, col_type in [("fee", "REAL DEFAULT 0.0"), ("tax", "REAL DEFAULT 0.0"), ("exchange_rate", "REAL DEFAULT 1.0")]:
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        for col_name, col_type in [("fee", "REAL DEFAULT 0.0"), ("tax", "REAL DEFAULT 0.0")]:
            try:
                conn.execute(f"ALTER TABLE approvals ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        for table in ("trades", "approvals"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN strategy_id TEXT")
            except sqlite3.OperationalError:
                pass
        for col_name, col_type in (
            ("strategy_version", "INTEGER"),
            ("profile_hash", "TEXT"),
            ("managed_order_id", "INTEGER"),
            ("decision_id", "INTEGER"),
            ("position_id", "INTEGER"),
            ("client_order_key", "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE approvals ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("ALTER TABLE scanned_candidates ADD COLUMN strategy_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            INSERT OR IGNORE INTO strategy_schedules (strategy_id, enabled, auto_approve)
            SELECT id, selected, 0 FROM ai_strategies
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    init_db()
    conn = connect_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    init_db()
    conn = connect_db()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    init_db()
    conn = connect_db()
    try:
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    init_db()
    conn = connect_db()
    try:
        item = conn.execute(query, params).fetchone()
    finally:
        conn.close()
    return dict(item) if item else None


def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    init_db()
    conn = connect_db()
    try:
        cur = conn.execute(query, params)
        conn.commit()
        return int(cur.lastrowid or cur.rowcount or 0)
    finally:
        conn.close()


def create_managed_order(data: dict[str, Any]) -> int:
    init_db()
    now = now_text()
    conn = connect_db()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO managed_orders (
                    client_order_key, broker_order_no, approval_id, strategy_id,
                    symbol, action, requested_qty, requested_price, filled_qty,
                    avg_fill_price, status, last_error, broker_payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["client_order_key"], data.get("broker_order_no"), data.get("approval_id"),
                    data.get("strategy_id"), data["symbol"], data["action"],
                    float(data.get("requested_qty") or 0), float(data.get("requested_price") or 0),
                    float(data.get("filled_qty") or 0), data.get("avg_fill_price"),
                    data.get("status") or "created", data.get("last_error"),
                    json.dumps(data.get("broker_payload"), ensure_ascii=False, default=str)
                    if data.get("broker_payload") is not None else None,
                    data.get("created_at") or now, data.get("updated_at") or now,
                ),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        existing = row(
            "SELECT id FROM managed_orders WHERE client_order_key=?",
            (str(data["client_order_key"]),),
        )
        if existing:
            return int(existing["id"])
        raise
    finally:
        conn.close()


def get_managed_order_by_key(client_order_key: str) -> dict[str, Any] | None:
    return row("SELECT * FROM managed_orders WHERE client_order_key=?", (str(client_order_key),))


def update_managed_order(order_id: int, **values: Any) -> bool:
    allowed = {
        "broker_order_no", "approval_id", "filled_qty", "avg_fill_price",
        "status", "last_error", "broker_payload", "requested_qty", "requested_price",
    }
    fields = {key: value for key, value in values.items() if key in allowed}
    if not fields:
        return False
    if "broker_payload" in fields and fields["broker_payload"] is not None:
        fields["broker_payload"] = json.dumps(fields["broker_payload"], ensure_ascii=False, default=str)
    fields["updated_at"] = now_text()
    assignments = ", ".join(f"{key}=?" for key in fields)
    params = tuple(fields.values()) + (int(order_id),)
    return execute(f"UPDATE managed_orders SET {assignments} WHERE id=?", params) > 0


def update_managed_order_by_broker_no(order_no: str, **values: Any) -> bool:
    item = row(
        "SELECT id FROM managed_orders WHERE broker_order_no=? ORDER BY id DESC LIMIT 1",
        (str(order_no),),
    )
    return update_managed_order(int(item["id"]), **values) if item else False
