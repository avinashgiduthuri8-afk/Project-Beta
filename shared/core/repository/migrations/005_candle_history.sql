-- V2 Migration 005 — Market Candles Cache
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Market Candles ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_candles (
    pair      TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    REAL NOT NULL,
    PRIMARY KEY (pair, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_market_candles_lookup 
ON market_candles (pair, timeframe, timestamp DESC);

-- Record this migration
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (5, datetime('now'), 'Market candles cache table for warm-up resilience');
