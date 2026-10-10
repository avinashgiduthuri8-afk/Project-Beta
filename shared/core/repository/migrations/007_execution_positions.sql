-- V2 Migration 006 — Execution Positions & State Tracking
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Ensure positions table exists
CREATE TABLE IF NOT EXISTS positions (
    id               TEXT PRIMARY KEY,
    bot              TEXT NOT NULL,
    coin             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    qty              REAL NOT NULL,
    entry_price      REAL NOT NULL,
    entry_time       TEXT NOT NULL,
    current_price    REAL,
    unrealised_pnl   REAL,
    stop_loss        REAL,
    take_profit      REAL,
    mode             TEXT NOT NULL,
    signal_id        TEXT,
    status           TEXT NOT NULL DEFAULT 'OPEN',
    exit_price       REAL,
    exit_reason      TEXT,
    closed_at        TEXT,
    side               TEXT DEFAULT 'BUY',
    realized_pnl       REAL DEFAULT 0.0,
    stop_loss_order_id TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

CREATE INDEX IF NOT EXISTS idx_positions_bot_status ON positions (bot, status);
CREATE INDEX IF NOT EXISTS idx_positions_pair_status ON positions (pair, status);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (6, datetime('now'), 'Execution positions and bracket tracking');
