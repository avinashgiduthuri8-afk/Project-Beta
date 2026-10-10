-- V2 Migration 002 — AI Intelligence Tables
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── AI Analyses Table ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_analyses (
    id                     TEXT PRIMARY KEY,
    signal_id              TEXT NOT NULL,
    coin                   TEXT NOT NULL,
    pair                   TEXT NOT NULL,
    recommendation         TEXT NOT NULL,
    confidence_score       INTEGER NOT NULL,
    trend_evaluation       TEXT NOT NULL,
    momentum_evaluation    TEXT NOT NULL,
    volume_evaluation      TEXT NOT NULL,
    setup_quality          TEXT NOT NULL,
    market_regime          TEXT NOT NULL,
    risk_reward_assessment TEXT NOT NULL,
    supporting_factors     TEXT NOT NULL,
    conflicts              TEXT NOT NULL,
    risk_factors           TEXT NOT NULL,
    suggested_adjustments  TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    execution_latency_ms   REAL NOT NULL,
    analyzed_at            TEXT NOT NULL,
    raw_response           TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals (id)
);

CREATE INDEX IF NOT EXISTS idx_ai_analyses_signal_id      ON ai_analyses (signal_id);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_coin           ON ai_analyses (coin);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_recommendation ON ai_analyses (recommendation);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_confidence     ON ai_analyses (confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_analyzed_at    ON ai_analyses (analyzed_at DESC);

-- ── Record this migration ─────────────────────────────────────────────────────
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (2, datetime('now'), 'AI Intelligence tables: ai_analyses');
