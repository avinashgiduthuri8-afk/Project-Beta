-- V2 Migration 007 — Post-Trade Journal & Analytics
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;

-- ── Trade Journal Table ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_journal (
    id                   TEXT PRIMARY KEY,
    position_id          TEXT NOT NULL,
    bot_name             TEXT NOT NULL,
    pair                 TEXT NOT NULL,
    side                 TEXT NOT NULL DEFAULT 'BUY',
    entry_price          REAL NOT NULL,
    exit_price           REAL NOT NULL,
    quantity             REAL NOT NULL,
    entry_timestamp      TEXT NOT NULL,
    exit_timestamp       TEXT NOT NULL,
    duration_seconds     INTEGER NOT NULL,
    exit_reason          TEXT NOT NULL,
    gross_pnl            REAL NOT NULL,
    exchange_fee         REAL NOT NULL,
    gst_tax              REAL NOT NULL,
    tds_194s             REAL NOT NULL,
    slippage_cost        REAL NOT NULL,
    total_statutory_drag REAL NOT NULL,
    net_pnl              REAL NOT NULL,
    net_pnl_pct          REAL NOT NULL,
    mfe                  REAL,
    mae                  REAL,
    tags                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_journal_position_id ON trade_journal (position_id);
CREATE INDEX IF NOT EXISTS idx_trade_journal_bot_name    ON trade_journal (bot_name);
CREATE INDEX IF NOT EXISTS idx_trade_journal_pair        ON trade_journal (pair);
CREATE INDEX IF NOT EXISTS idx_trade_journal_exit_time   ON trade_journal (exit_timestamp DESC);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (7, datetime('now'), 'Post-trade journal & statutory tax tracking');
