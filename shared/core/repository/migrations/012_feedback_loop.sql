-- V2 Migration 010 — Autonomous Feedback Loop & Active Calibrations Cache
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;

-- ── Feedback Audit Trail Table ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feedback_audit_trail (
    id                     TEXT PRIMARY KEY,
    cycle_id               TEXT NOT NULL,
    bot_name               TEXT NOT NULL,
    pair                   TEXT,
    action_taken           TEXT NOT NULL,
    previous_multiplier    REAL NOT NULL,
    new_multiplier         REAL NOT NULL,
    previous_threshold     REAL NOT NULL,
    new_threshold          REAL NOT NULL,
    validation_backtest_id TEXT,
    status                 TEXT NOT NULL,
    created_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_audit_cycle ON feedback_audit_trail (cycle_id);
CREATE INDEX IF NOT EXISTS idx_feedback_audit_bot   ON feedback_audit_trail (bot_name);
CREATE INDEX IF NOT EXISTS idx_feedback_audit_date  ON feedback_audit_trail (created_at DESC);

-- ── Active Calibrations Cache Table ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS active_calibrations_cache (
    bot_name          TEXT PRIMARY KEY,
    weight_multiplier REAL NOT NULL DEFAULT 1.0,
    strict_threshold  REAL NOT NULL DEFAULT 85.0,
    updated_at        TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (10, datetime('now'), 'Autonomous recursive feedback audit trail & active calibrations cache');
