-- V2 Migration 003
ALTER TABLE positions ADD COLUMN side TEXT;
INSERT OR IGNORE INTO schema_version (version, applied_at, description) VALUES (3, datetime('now'), 'Add side to positions');