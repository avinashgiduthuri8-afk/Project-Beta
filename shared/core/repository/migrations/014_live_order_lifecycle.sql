-- V2 Migration 014 — Live Order Lifecycle & State Machine Persistence
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    client_order_id TEXT UNIQUE NOT NULL,
    exchange_order_id TEXT,
    bot TEXT NOT NULL,
    coin TEXT NOT NULL,
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    req_qty REAL NOT NULL,
    price REAL NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0.0,
    remaining_qty REAL NOT NULL DEFAULT 0.0,
    avg_price REAL NOT NULL DEFAULT 0.0,
    state TEXT NOT NULL DEFAULT 'CREATED',
    position_id TEXT,
    signal_id TEXT,
    mode TEXT NOT NULL DEFAULT 'LIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_exchange_order_id ON orders(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
CREATE INDEX IF NOT EXISTS idx_orders_bot ON orders(bot);

CREATE TABLE IF NOT EXISTS order_state_transitions (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    reason TEXT,
    metadata TEXT,
    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_order_transitions_order_id ON order_state_transitions(order_id);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (14, datetime('now'), 'Add orders and order_state_transitions tables for live order lifecycle management');

