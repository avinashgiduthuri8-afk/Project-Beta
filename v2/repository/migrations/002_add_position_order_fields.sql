-- V2 Migration 002 — Position Order & Fill Tracking Fields
-- Applied automatically at startup by v2/repository/db.py

ALTER TABLE positions ADD COLUMN filled_qty REAL;
ALTER TABLE positions ADD COLUMN exchange_order_id TEXT;
ALTER TABLE positions ADD COLUMN client_order_id TEXT;
ALTER TABLE positions ADD COLUMN exit_order_id TEXT;

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (2, datetime('now'), 'Add filled_qty, exchange_order_id, client_order_id, exit_order_id to positions table');

