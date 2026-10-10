-- Phase 4 — QPB portfolio decision ledger.
-- This migration is applied to the service-specific database opened by the process.

CREATE TABLE IF NOT EXISTS qpb_signal_decisions (
    signal_id        TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    strategy         TEXT NOT NULL,
    strategy_name    TEXT NOT NULL,
    status           TEXT NOT NULL,
    ranking_score    REAL,
    allocated_amount REAL NOT NULL DEFAULT 0.0,
    reason_code      TEXT,
    reason           TEXT,
    position_id      TEXT,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qpb_decisions_status
    ON qpb_signal_decisions(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qpb_decisions_ranking
    ON qpb_signal_decisions(ranking_score DESC, signal_id ASC);
CREATE INDEX IF NOT EXISTS idx_qpb_decisions_strategy
    ON qpb_signal_decisions(strategy, status);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (15, datetime('now'), 'Phase 4 QPB persistent signal decisions and rankings');
