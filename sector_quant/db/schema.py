"""Relational database schema and entity definitions for the Securities Master."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class ExchangeType(str, Enum):
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    NSE = "NSE"
    BSE = "BSE"
    LSE = "LSE"
    SIMULATED = "SIMULATED"


class DataVendorType(str, Enum):
    YAHOO = "YAHOO"
    ALPHA_VANTAGE = "ALPHA_VANTAGE"
    BLOOMBERG = "BLOOMBERG"
    REUTERS = "REUTERS"
    ZERODHA_KITE = "ZERODHA_KITE"
    HISTORIC_CSV = "HISTORIC_CSV"
    SYNTHETIC = "SYNTHETIC"


class ActionType(str, Enum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    BONUS = "BONUS"
    RIGHTS = "RIGHTS"


@dataclass
class SectorType:
    id: Optional[int]
    code: str
    name: str
    benchmark_symbol: Optional[str] = None
    description: Optional[str] = None


@dataclass
class SymbolType:
    id: Optional[int]
    ticker: str
    exchange_id: int
    sector_id: Optional[int]
    security_name: str
    currency: str = "USD"
    is_active: bool = True
    created_at: Optional[datetime] = None


@dataclass
class DailyPriceRecord:
    id: Optional[int]
    symbol_id: int
    price_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    adj_close_price: float
    volume: int
    adj_factor: float = 1.0


@dataclass
class IntradayPriceRecord:
    id: Optional[int]
    symbol_id: int
    price_timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int


@dataclass
class CorporateActionRecord:
    id: Optional[int]
    symbol_id: int
    ex_date: date
    action_type: ActionType
    value: float  # e.g., dividend cash amount or split ratio numerator
    split_ratio: float = 1.0
    cash_amount: float = 0.0
    notes: Optional[str] = None


CREATE_TABLES_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS exchange (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_vendor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    website_url TEXT,
    support_email TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sector (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    benchmark_symbol TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symbol (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    exchange_id INTEGER NOT NULL,
    sector_id INTEGER,
    security_name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(exchange_id) REFERENCES exchange(id),
    FOREIGN KEY(sector_id) REFERENCES sector(id)
);

CREATE INDEX IF NOT EXISTS idx_symbol_ticker ON symbol(ticker);
CREATE INDEX IF NOT EXISTS idx_symbol_sector ON symbol(sector_id);

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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol_id, price_date),
    FOREIGN KEY(symbol_id) REFERENCES symbol(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_price_sym_date ON daily_price(symbol_id, price_date);

CREATE TABLE IF NOT EXISTS intraday_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL,
    price_timestamp TIMESTAMP NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL,
    volume INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol_id, price_timestamp),
    FOREIGN KEY(symbol_id) REFERENCES symbol(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intraday_price_sym_time ON intraday_price(symbol_id, price_timestamp);

CREATE TABLE IF NOT EXISTS corporate_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL,
    ex_date DATE NOT NULL,
    action_type TEXT NOT NULL,
    value REAL NOT NULL,
    split_ratio REAL NOT NULL DEFAULT 1.0,
    cash_amount REAL NOT NULL DEFAULT 0.0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(symbol_id) REFERENCES symbol(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_corp_action_sym_date ON corporate_action(symbol_id, ex_date);
"""

