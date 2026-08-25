-- V2 Migration 001 — Core Tables
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Schema version tracking ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT NOT NULL
);

-- ── Signals ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id               TEXT PRIMARY KEY,
    coin             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    market_state     TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    priority         TEXT NOT NULL,
    risk_level       TEXT NOT NULL,
    score            INTEGER NOT NULL,
    confidence       INTEGER NOT NULL,
    coin_class       TEXT,
    mtf_alignment    INTEGER NOT NULL DEFAULT 0,
    generated_at     TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    expired_at       TEXT,
    expiry_reason    TEXT,
    source_bot       TEXT NOT NULL DEFAULT 'scanner_v1',
    raw_payload      TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_priority     ON signals (priority);
CREATE INDEX IF NOT EXISTS idx_signals_coin         ON signals (coin);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at ON signals (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_expires_at   ON signals (expires_at);

-- ── Positions ────────────────────────────────────────────────────────────────
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
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

CREATE INDEX IF NOT EXISTS idx_positions_bot    ON positions (bot);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (status);
CREATE INDEX IF NOT EXISTS idx_positions_coin   ON positions (coin);

-- ── Trades ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id               TEXT PRIMARY KEY,
    position_id      TEXT NOT NULL,
    bot              TEXT NOT NULL,
    coin             TEXT NOT NULL,
    pair             TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    exit_price       REAL NOT NULL,
    qty              REAL NOT NULL,
    pnl              REAL NOT NULL,
    pnl_pct          REAL NOT NULL,
    entry_time       TEXT NOT NULL,
    exit_time        TEXT NOT NULL,
    exit_reason      TEXT NOT NULL,
    mode             TEXT NOT NULL,
    signal_id        TEXT,
    FOREIGN KEY (position_id) REFERENCES positions (id),
    FOREIGN KEY (signal_id)   REFERENCES signals   (id)
);

CREATE INDEX IF NOT EXISTS idx_trades_bot       ON trades (bot);
CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades (exit_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_coin      ON trades (coin);
CREATE INDEX IF NOT EXISTS idx_trades_exit_reason ON trades (exit_reason);

-- ── Metrics snapshots ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id               TEXT PRIMARY KEY,
    captured_at      TEXT NOT NULL,
    total_aum        REAL,
    total_deployed   REAL,
    total_cash       REAL,
    total_unrealised REAL,
    total_realised   REAL,
    daily_pnl        REAL,
    capital_util_pct REAL,
    per_bot_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_metrics_captured_at ON metrics_snapshots (captured_at DESC);

-- ── Bot state snapshots ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_snapshots (
    id               TEXT PRIMARY KEY,
    bot              TEXT NOT NULL,
    mode             TEXT NOT NULL,
    status           TEXT NOT NULL,
    cash_balance     REAL,
    deployed_capital REAL,
    open_positions   INTEGER,
    total_pnl        REAL,
    health_score     INTEGER,
    captured_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bot_snapshots_bot ON bot_snapshots (bot, captured_at DESC);

-- ── Event log (append-only audit trail) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_log (
    id             TEXT PRIMARY KEY,
    event_type     TEXT NOT NULL,
    source_service TEXT,
    entity_id      TEXT,
    payload_json   TEXT NOT NULL,
    logged_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_log_type      ON event_log (event_type);
CREATE INDEX IF NOT EXISTS idx_event_log_entity    ON event_log (entity_id);
CREATE INDEX IF NOT EXISTS idx_event_log_logged_at ON event_log (logged_at DESC);

-- ── Record this migration ─────────────────────────────────────────────────────
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (1, datetime('now'), 'Core tables: signals, positions, trades, metrics, event_log');
