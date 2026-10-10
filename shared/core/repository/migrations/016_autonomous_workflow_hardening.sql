-- Durable state required by the three-service autonomous workflow.

ALTER TABLE positions ADD COLUMN peak_price REAL;
ALTER TABLE positions ADD COLUMN trough_price REAL;
ALTER TABLE positions ADD COLUMN market_regime TEXT;

ALTER TABLE feedback_audit_trail ADD COLUMN validation_candle_count INTEGER;
ALTER TABLE feedback_audit_trail ADD COLUMN validation_oos_candle_count INTEGER;
ALTER TABLE feedback_audit_trail ADD COLUMN validation_trade_count INTEGER;
ALTER TABLE feedback_audit_trail ADD COLUMN validation_profit_factor REAL;
ALTER TABLE feedback_audit_trail ADD COLUMN validation_reason TEXT;

CREATE TABLE IF NOT EXISTS calibration_canary_state (
    bot_name            TEXT PRIMARY KEY,
    promotion_id        TEXT NOT NULL,
    promoted_at         TEXT NOT NULL,
    consecutive_losses  INTEGER NOT NULL DEFAULT 0,
    active              INTEGER NOT NULL DEFAULT 1,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_calibration_outcomes (
    trade_id       TEXT PRIMARY KEY,
    bot_name       TEXT NOT NULL,
    promotion_id   TEXT NOT NULL,
    processed_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calibration_outcomes_bot
ON processed_calibration_outcomes (bot_name, processed_at DESC);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (16, datetime('now'), 'Durable canary outcomes and position excursions');
