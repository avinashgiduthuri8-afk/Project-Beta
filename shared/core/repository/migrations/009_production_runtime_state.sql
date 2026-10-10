-- V2 Migration 007 — Production Runtime State & Emergency Controller Persistence
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Runtime operational state persistence table
CREATE TABLE IF NOT EXISTS production_runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT DEFAULT 'SYSTEM'
);

-- Index for fast lookup by key
CREATE INDEX IF NOT EXISTS idx_prod_state_key ON production_runtime_state (key);

-- Seed initial defaults if table is empty
INSERT OR IGNORE INTO production_runtime_state (key, value, updated_at, updated_by)
VALUES 
    ('v2_deployment_mode', 'PAPER', datetime('now'), 'MIGRATION_007'),
    ('v2_trading_enabled', 'true', datetime('now'), 'MIGRATION_007'),
    ('circuit_breaker_status', 'NORMAL', datetime('now'), 'MIGRATION_007'),
    ('circuit_breaker_reason', '', datetime('now'), 'MIGRATION_007'),
    ('emergency_stop', 'false', datetime('now'), 'MIGRATION_007'),
    ('last_kill_switch_at', '', datetime('now'), 'MIGRATION_007'),
    ('last_resumed_at', '', datetime('now'), 'MIGRATION_007');

-- Record migration version
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (7, datetime('now'), 'Production Runtime State & Circuit Breaker Persistence');
