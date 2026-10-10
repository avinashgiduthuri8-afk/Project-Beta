-- V2 Migration 009 — Backtest Results & Strategy Optimization
-- Applied automatically at startup by v2/repository/db.py

PRAGMA journal_mode=WAL;

-- ── Backtest Summary Runs Table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backtest_runs (
    id            TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    pair          TEXT NOT NULL,
    timeframe     TEXT NOT NULL DEFAULT '5m',
    start_time    TEXT NOT NULL,
    end_time      TEXT NOT NULL,
    total_trades  INTEGER NOT NULL,
    win_rate      REAL NOT NULL,
    profit_factor REAL NOT NULL,
    max_drawdown  REAL NOT NULL,
    sharpe_ratio  REAL NOT NULL,
    cagr          REAL NOT NULL DEFAULT 0.0,
    parameters    TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy ON backtest_runs (strategy_name);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_pair     ON backtest_runs (pair);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_created  ON backtest_runs (created_at DESC);

-- ── Backtest Executed Trades Table ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backtest_trades (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    pair           TEXT NOT NULL,
    side           TEXT NOT NULL DEFAULT 'BUY',
    entry_time     TEXT NOT NULL,
    exit_time      TEXT NOT NULL,
    entry_price    REAL NOT NULL,
    exit_price     REAL NOT NULL,
    quantity       REAL NOT NULL,
    gross_pnl      REAL NOT NULL,
    net_pnl        REAL NOT NULL,
    statutory_drag REAL NOT NULL,
    exit_reason    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON backtest_trades (run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_pair   ON backtest_trades (pair);

INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (9, datetime('now'), 'Historical backtest runs & trade simulation logs');
