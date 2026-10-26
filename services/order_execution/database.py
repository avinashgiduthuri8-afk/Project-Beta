"""Connection and versioned schema for isolated execution state."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


EXECUTION_SCHEMA_VERSION = 1

EXECUTION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_schema_version (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL
);
INSERT INTO execution_schema_version(singleton,version) VALUES(1,1)
ON CONFLICT(singleton) DO UPDATE SET version=MAX(version, excluded.version);
CREATE TABLE IF NOT EXISTS execution_order_intents (
    intent_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL UNIQUE,
    canonical_symbol TEXT NOT NULL,
    exchange TEXT NOT NULL CHECK(exchange IN ('NSE','BSE')),
    security_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    order_type TEXT NOT NULL,
    product_type TEXT NOT NULL CHECK(product_type='CNC'),
    limit_price TEXT,
    trigger_price TEXT,
    status TEXT NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    average_price TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_broker_orders (
    broker_order_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL UNIQUE REFERENCES execution_order_intents(intent_id),
    correlation_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    requested_quantity INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL,
    average_price TEXT,
    error_code TEXT,
    error_message TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL REFERENCES execution_order_intents(intent_id),
    broker_order_id TEXT,
    previous_status TEXT,
    status TEXT NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    average_price TEXT,
    error_code TEXT,
    error_message TEXT,
    occurred_at TEXT NOT NULL
);
"""


async def open_execution_database(
    path: str, *, beta_database_path: str
) -> aiosqlite.Connection:
    resolved = Path(path).resolve()
    beta_resolved = Path(beta_database_path).resolve()
    if resolved == beta_resolved:
        raise ValueError("EXECUTION_DATABASE_MUST_BE_SEPARATE")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(resolved, timeout=5.0)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn


async def initialize_execution_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(EXECUTION_SCHEMA_SQL)
    await conn.commit()

