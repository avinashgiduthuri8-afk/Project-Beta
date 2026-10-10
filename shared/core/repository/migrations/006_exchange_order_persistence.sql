-- V2 Migration 006 — Exchange Order ID Persistence & Order Tracking
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;

-- Add exchange order identity columns to positions
ALTER TABLE positions ADD COLUMN exchange_order_id TEXT;
ALTER TABLE positions ADD COLUMN client_order_id TEXT;
ALTER TABLE positions ADD COLUMN filled_qty REAL;

-- Add exchange order identity columns to trades
ALTER TABLE trades ADD COLUMN exchange_order_id TEXT;
ALTER TABLE trades ADD COLUMN client_order_id TEXT;

-- Record this migration
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (6, datetime('now'), 'Add exchange_order_id, client_order_id, and filled_qty to positions and trades');
