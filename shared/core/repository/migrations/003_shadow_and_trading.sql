-- V2 Migration 003 — Shadow Mode & Divergence Tracking Tables
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Shadow Trades Table ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shadow_trades (
    id                   TEXT PRIMARY KEY,
    signal_id            TEXT NOT NULL,
    bot                  TEXT NOT NULL,
    coin                 TEXT NOT NULL,
    pair                 TEXT NOT NULL,
    entry_price          REAL NOT NULL,
    qty                  REAL NOT NULL,
    amount               REAL NOT NULL,
    stop_loss            REAL,
    take_profit          REAL,
    ai_recommendation    TEXT,
    status               TEXT NOT NULL DEFAULT 'OPEN',
    simulated_exit_price REAL,
    simulated_pnl        REAL,
    simulated_pnl_pct    REAL,
    exit_reason          TEXT,
    created_at           TEXT NOT NULL,
    closed_at            TEXT,
    raw_adjustments      TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_signal_id  ON shadow_trades (signal_id);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_bot        ON shadow_trades (bot);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_coin       ON shadow_trades (coin);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_status     ON shadow_trades (status);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_created_at ON shadow_trades (created_at DESC);

-- ── Decision Divergences Table ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decision_divergences (
    id               TEXT PRIMARY KEY,
    signal_id        TEXT NOT NULL,
    bot              TEXT NOT NULL,
    coin             TEXT NOT NULL,
    v1_action        TEXT NOT NULL,
    v2_action        TEXT NOT NULL,
    divergence_type  TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detected_at      TEXT NOT NULL,
    v1_pnl           REAL,
    v2_simulated_pnl REAL,
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

CREATE INDEX IF NOT EXISTS idx_divergences_signal_id   ON decision_divergences (signal_id);
CREATE INDEX IF NOT EXISTS idx_divergences_type        ON decision_divergences (divergence_type);
CREATE INDEX IF NOT EXISTS idx_divergences_detected_at ON decision_divergences (detected_at DESC);

-- ── Record this migration ─────────────────────────────────────────────────────
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (3, datetime('now'), 'Shadow mode & divergence tracking: shadow_trades, decision_divergences');
