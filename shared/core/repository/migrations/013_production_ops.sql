-- =============================================================================
-- Migration 011: Production Operations, Runtime State & Shadow Logs Schema
-- =============================================================================

CREATE TABLE IF NOT EXISTS production_ops_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    deployment_mode     TEXT NOT NULL DEFAULT 'SHADOW',  -- SHADOW, PAPER, LIVE_MICROCASH
    is_active           INTEGER NOT NULL DEFAULT 1,
    global_kill_switch  INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

INSERT OR IGNORE INTO production_ops_state (id, deployment_mode, is_active, global_kill_switch, updated_at)
VALUES (1, 'SHADOW', 1, 0, datetime('now'));

CREATE TABLE IF NOT EXISTS shadow_trade_logs (
    id                          TEXT PRIMARY KEY,
    bot_name                    TEXT NOT NULL,
    pair                        TEXT NOT NULL,
    simulated_entry_price       REAL NOT NULL,
    real_orderbook_entry_price  REAL NOT NULL,
    slippage_divergence_pct     REAL NOT NULL,
    timestamp                   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_logs_bot ON shadow_trade_logs (bot_name);
CREATE INDEX IF NOT EXISTS idx_shadow_logs_time ON shadow_trade_logs (timestamp);
