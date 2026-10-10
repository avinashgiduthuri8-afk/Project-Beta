-- V2 Migration 004 — Production Fleet (STE, HDA, VCP, BBS) & CoinDCX Sub-Account Migration
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Update any legacy position / trade references if they exist
UPDATE positions SET bot = 'STE' WHERE bot = 'MTB';
UPDATE positions SET bot = 'HDA' WHERE bot = 'PMB';
UPDATE positions SET bot = 'BBS' WHERE bot = 'VGX';

UPDATE trades SET bot = 'STE' WHERE bot = 'MTB';
UPDATE trades SET bot = 'HDA' WHERE bot = 'PMB';
UPDATE trades SET bot = 'BBS' WHERE bot = 'VGX';

-- Ensure dedicated indexes for the 4 production bots
CREATE INDEX IF NOT EXISTS idx_positions_prod_bot ON positions (bot, status);
CREATE INDEX IF NOT EXISTS idx_trades_prod_bot    ON trades (bot, exit_time DESC);

-- Record this migration
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (4, datetime('now'), 'Production Fleet (STE, HDA, VCP, BBS) and Sub-Account architecture');
