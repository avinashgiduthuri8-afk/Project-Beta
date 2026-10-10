-- V2 Migration 008 — Learning Engine & Dynamic Strategy Calibration
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;

-- ── Learning Insights Table ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS learning_insights (
    id                     TEXT PRIMARY KEY,
    bot_name               TEXT,
    pair                   TEXT,
    pattern_type           TEXT NOT NULL,
    severity               TEXT NOT NULL,
    lesson_summary         TEXT NOT NULL,
    recommended_adjustment TEXT NOT NULL,
    created_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_insights_bot_name   ON learning_insights (bot_name);
CREATE INDEX IF NOT EXISTS idx_learning_insights_pair       ON learning_insights (pair);
CREATE INDEX IF NOT EXISTS idx_learning_insights_pattern    ON learning_insights (pattern_type);
CREATE INDEX IF NOT EXISTS idx_learning_insights_created_at ON learning_insights (created_at DESC);

-- ── Strategy Calibrations Table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_calibrations (
    id                       TEXT PRIMARY KEY,
    bot_name                 TEXT UNIQUE NOT NULL,
    pair                     TEXT,
    weight_multiplier        REAL NOT NULL DEFAULT 1.0,
    min_confluence_threshold REAL NOT NULL DEFAULT 85.0,
    status                   TEXT NOT NULL DEFAULT 'ACTIVE',
    updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_calibrations_bot ON strategy_calibrations (bot_name);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (8, datetime('now'), 'Learning engine mistake detection & dynamic strategy calibrations');
