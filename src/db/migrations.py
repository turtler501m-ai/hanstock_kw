"""Small, ordered database migrations for safety-critical shared tables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n".join(statement.strip() for statement in self.statements)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        "unified_order_ledger",
        (
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_key TEXT NOT NULL UNIQUE,
                correlation_id TEXT NOT NULL,
                account_key TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'KR',
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
                order_type TEXT NOT NULL DEFAULT 'limit',
                time_in_force TEXT NOT NULL DEFAULT 'DAY',
                requested_qty INTEGER NOT NULL CHECK(requested_qty > 0),
                order_price REAL NOT NULL DEFAULT 0,
                filled_qty INTEGER NOT NULL DEFAULT 0 CHECK(filled_qty >= 0),
                average_fill_price REAL NOT NULL DEFAULT 0,
                broker_order_id TEXT,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                strategy_id TEXT,
                strategy_version INTEGER,
                signal_id TEXT,
                decision_id INTEGER,
                approval_id INTEGER,
                parent_order_id INTEGER,
                expires_at TEXT,
                submitted_at TEXT,
                completed_at TEXT,
                last_synced_at TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(parent_order_id) REFERENCES orders(id),
                CHECK(filled_qty <= requested_qty)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_orders_status_updated ON orders(status, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_broker_id ON orders(broker_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_approval_id ON orders(approval_id)",
            """
            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                actor TEXT NOT NULL DEFAULT 'system',
                reason TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, id)",
            """
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fill_key TEXT NOT NULL UNIQUE,
                order_id INTEGER NOT NULL,
                broker_fill_id TEXT,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                price REAL NOT NULL CHECK(price >= 0),
                fee REAL,
                tax REAL,
                cost_source TEXT NOT NULL DEFAULT 'unavailable',
                filled_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(order_id) REFERENCES orders(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id, filled_at)",
            """
            CREATE TABLE IF NOT EXISTS reconciliation_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_key TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'KR',
                symbol TEXT NOT NULL,
                broker_qty INTEGER NOT NULL,
                internal_qty INTEGER NOT NULL,
                difference_qty INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_reconciliation_open ON reconciliation_adjustments(status, created_at)",
        ),
    ),
    Migration(
        2,
        "order_runtime_and_positions",
        (
            """
            CREATE TABLE IF NOT EXISTS order_runtime_state (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                state TEXT NOT NULL CHECK(state IN ('recovering','reduce_only','ready')),
                reason TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS positions (
                account_key TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                net_cash_flow REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(account_key, market, symbol)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_positions_market_symbol ON positions(market, symbol)",
        ),
    ),
    Migration(
        3,
        "broker_order_identity",
        (
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_broker_identity_unique
               ON orders(account_key, market, broker_order_id)
               WHERE broker_order_id IS NOT NULL AND broker_order_id <> ''""",
        ),
    ),
    Migration(
        4,
        "dated_broker_order_identity",
        (
            "ALTER TABLE orders ADD COLUMN broker_order_date TEXT NOT NULL DEFAULT ''",
            "DROP INDEX IF EXISTS idx_orders_broker_identity_unique",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_broker_identity_unique
               ON orders(account_key, market, broker_order_date, broker_order_id)
               WHERE broker_order_id IS NOT NULL AND broker_order_id <> ''""",
        ),
    ),
    Migration(
        5,
        "audited_position_quantity_adjustments",
        (
            """
            CREATE TABLE IF NOT EXISTS position_quantity_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_key TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity_delta INTEGER NOT NULL,
                reconciliation_id INTEGER NOT NULL UNIQUE,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(reconciliation_id) REFERENCES reconciliation_adjustments(id)
            )
            """,
            """CREATE INDEX IF NOT EXISTS idx_position_quantity_adjustments_scope
               ON position_quantity_adjustments(account_key,market,symbol)""",
        ),
    ),
)


def apply_migrations(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row[0]): str(row[1])
        for row in conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    }
    for migration in MIGRATIONS:
        checksum = migration.checksum
        if migration.version in applied:
            if applied[migration.version] != checksum:
                raise RuntimeError(
                    f"database migration checksum mismatch: {migration.version} {migration.name}"
                )
            continue
        for statement in migration.statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, checksum) VALUES (?, ?, ?)",
            (migration.version, migration.name, checksum),
        )
