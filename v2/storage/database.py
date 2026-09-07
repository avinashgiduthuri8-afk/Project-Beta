"""SQLite WAL-mode 21-Table Schema Manager for Project-Beta V2."""

from __future__ import annotations

import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CREATE_21_TABLES_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. Signals Table
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    bot TEXT NOT NULL,
    coin TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    confluence_score REAL NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    meta_json TEXT
);

-- 2. Positions Table (Active & Historical)
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    bot TEXT NOT NULL,
    coin TEXT NOT NULL,
    pair TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    current_price REAL,
    unrealised_pnl REAL,
    stop_loss REAL,
    take_profit REAL,
    trailing_stop_pct REAL,
    trailing_peak_price REAL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    signal_id TEXT,
    exit_price REAL,
    exit_reason TEXT,
    closed_at TIMESTAMP,
    exchange_order_id TEXT,
    client_order_id TEXT,
    filled_qty REAL
);

-- 3. Trades Table (Realized Settlements)
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    bot TEXT NOT NULL,
    coin TEXT NOT NULL,
    pair TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    qty REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP NOT NULL,
    exit_reason TEXT NOT NULL,
    mode TEXT NOT NULL,
    signal_id TEXT,
    exchange_order_id TEXT,
    client_order_id TEXT
);

-- 4. Market Candles Cache
CREATE TABLE IF NOT EXISTS market_candles (
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (pair, timeframe, timestamp)
);

-- 5. AI Analyses Log
CREATE TABLE IF NOT EXISTS ai_analyses (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    model TEXT NOT NULL,
    confidence REAL NOT NULL,
    verdict TEXT NOT NULL,
    thesis TEXT,
    risks_json TEXT,
    catalysts_json TEXT,
    latency_ms REAL,
    created_at TIMESTAMP NOT NULL
);

-- 6. Strategy Calibrations & Feedback
CREATE TABLE IF NOT EXISTS strategy_calibrations (
    id TEXT PRIMARY KEY,
    bot TEXT NOT NULL,
    calibrated_at TIMESTAMP NOT NULL,
    c2_weight_chart REAL NOT NULL,
    c2_weight_indicator REAL NOT NULL,
    c2_weight_regime REAL NOT NULL,
    c2_weight_sentiment REAL NOT NULL,
    min_confluence_threshold REAL NOT NULL,
    risk_multiplier REAL NOT NULL,
    reason TEXT
);

-- 7. Event Log (Immutable System Audit Trail)
CREATE TABLE IF NOT EXISTS event_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- 8. Exchange Table
CREATE TABLE IF NOT EXISTS exchange (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata'
);

-- 9. Data Vendor Table
CREATE TABLE IF NOT EXISTS data_vendor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    website_url TEXT
);

-- 10. Sector Table
CREATE TABLE IF NOT EXISTS sector (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    benchmark_symbol TEXT
);

-- 11. Symbol Table
CREATE TABLE IF NOT EXISTS symbol (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    exchange_id INTEGER NOT NULL,
    sector_id INTEGER,
    security_name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    is_active INTEGER NOT NULL DEFAULT 1
);

-- 12. Daily Price Table
CREATE TABLE IF NOT EXISTS daily_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL,
    adj_close_price REAL NOT NULL,
    volume INTEGER NOT NULL,
    adj_factor REAL NOT NULL DEFAULT 1.0,
    UNIQUE(symbol_id, price_date)
);

-- 13. Intraday Price Table
CREATE TABLE IF NOT EXISTS intraday_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL,
    price_timestamp TIMESTAMP NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL,
    volume INTEGER NOT NULL,
    UNIQUE(symbol_id, price_timestamp)
);

-- 14. Corporate Action Table
CREATE TABLE IF NOT EXISTS corporate_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL,
    ex_date DATE NOT NULL,
    action_type TEXT NOT NULL,
    value REAL NOT NULL,
    split_ratio REAL NOT NULL DEFAULT 1.0,
    cash_amount REAL NOT NULL DEFAULT 0.0
);

-- 15. Bot Fleet Status Table
CREATE TABLE IF NOT EXISTS bot_fleet_status (
    bot_id TEXT PRIMARY KEY,
    archetype TEXT NOT NULL,
    mode TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    allocated_capital REAL NOT NULL,
    last_heartbeat TIMESTAMP NOT NULL
);

-- 16. Risk Circuit Breakers Table
CREATE TABLE IF NOT EXISTS risk_circuit_breakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    breaker_name TEXT NOT NULL,
    state TEXT NOT NULL,
    tripped_at TIMESTAMP,
    cooldown_seconds REAL NOT NULL DEFAULT 60.0,
    reason TEXT
);

-- 17. Portfolio Snapshots Table
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    total_equity REAL NOT NULL,
    cash_balance REAL NOT NULL,
    unrealised_pnl REAL NOT NULL,
    realised_pnl REAL NOT NULL,
    gross_exposure REAL NOT NULL
);

-- 18. Tax Friction Ledger Table
CREATE TABLE IF NOT EXISTS tax_friction_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    stt_paid REAL NOT NULL DEFAULT 0.0,
    brokerage_paid REAL NOT NULL DEFAULT 0.0,
    stamp_duty REAL NOT NULL DEFAULT 0.0,
    gst_paid REAL NOT NULL DEFAULT 0.0,
    total_friction REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP NOT NULL
);

-- 19. Walk Forward Metrics Table
CREATE TABLE IF NOT EXISTS walk_forward_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_date TIMESTAMP NOT NULL,
    in_sample_sharpe REAL NOT NULL,
    out_of_sample_sharpe REAL NOT NULL,
    walk_forward_efficiency REAL NOT NULL,
    alpha_decay_detected INTEGER NOT NULL DEFAULT 0
);

-- 20. Feedback Adjustments Table
CREATE TABLE IF NOT EXISTS feedback_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot TEXT NOT NULL,
    adjustment_type TEXT NOT NULL,
    old_value REAL NOT NULL,
    new_value REAL NOT NULL,
    applied_at TIMESTAMP NOT NULL
);

-- 21. System Audit Log Table
CREATE TABLE IF NOT EXISTS system_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_level TEXT NOT NULL,
    module TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Indices
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_trades_bot ON trades(bot);
CREATE INDEX IF NOT EXISTS idx_daily_price_sym ON daily_price(symbol_id, price_date);
"""


class DatabaseManager:
    """Manages SQLite database creation and WAL initialization."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        if self._conn is None or self.db_path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executescript(CREATE_21_TABLES_SQL)
        conn.commit()

