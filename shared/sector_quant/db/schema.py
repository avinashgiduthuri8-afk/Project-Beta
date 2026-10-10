"""
sector_quant.db.schema — Relational schema and queries for Securities Master.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize relational Securities Master tables."""
    cursor = conn.cursor()
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS sectors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        benchmark TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS symbols (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        sector_id INTEGER NOT NULL,
        exchange TEXT NOT NULL DEFAULT 'NSE',
        currency TEXT NOT NULL DEFAULT 'INR',
        is_active INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (sector_id) REFERENCES sectors (id)
    );

    CREATE TABLE IF NOT EXISTS daily_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol_id INTEGER NOT NULL,
        price_date TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume INTEGER NOT NULL,
        adj_close REAL,
        FOREIGN KEY (symbol_id) REFERENCES symbols (id),
        UNIQUE(symbol_id, price_date)
    );

    CREATE INDEX IF NOT EXISTS idx_prices_sym_date ON daily_prices (symbol_id, price_date);

    CREATE TABLE IF NOT EXISTS corporate_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol_id INTEGER NOT NULL,
        action_date TEXT NOT NULL,
        action_type TEXT NOT NULL,
        value REAL NOT NULL,
        FOREIGN KEY (symbol_id) REFERENCES symbols (id)
    );
    """)
    conn.commit()


def insert_sector(conn: sqlite3.Connection, name: str, benchmark: str = "") -> int:
    """Insert or retrieve sector id."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO sectors (name, benchmark) VALUES (?, ?)",
        (name, benchmark),
    )
    conn.commit()
    cursor.execute("SELECT id FROM sectors WHERE name = ?", (name,))
    row = cursor.fetchone()
    return int(row[0])


def insert_symbol(
    conn: sqlite3.Connection,
    ticker: str,
    name: str,
    sector_id: int,
    exchange: str = "NSE",
    currency: str = "INR",
    is_active: bool = True,
) -> int:
    """Insert or retrieve symbol id."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO symbols (ticker, name, sector_id, exchange, currency, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ticker.upper(),
            name,
            sector_id,
            exchange.upper(),
            currency.upper(),
            1 if is_active else 0,
        ),
    )
    conn.commit()
    cursor.execute("SELECT id FROM symbols WHERE ticker = ?", (ticker.upper(),))
    row = cursor.fetchone()
    return int(row[0])


def insert_daily_price(
    conn: sqlite3.Connection,
    symbol_id: int,
    price_date: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    adj_close: float | None = None,
) -> None:
    """Insert or replace a daily price bar."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO daily_prices (symbol_id, price_date, open, high, low, close, volume, adj_close)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol_id, price_date, open_, high, low, close, volume, adj_close or close),
    )
    conn.commit()


def insert_corporate_action(
    conn: sqlite3.Connection,
    symbol_id: int,
    action_date: str,
    action_type: str,
    value: float,
) -> None:
    """Record dividend or split corporate action."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO corporate_actions (symbol_id, action_date, action_type, value)
        VALUES (?, ?, ?, ?)
        """,
        (symbol_id, action_date, action_type.upper(), value),
    )
    conn.commit()


def get_symbol(conn: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    """Query symbol info by ticker."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.id, s.ticker, s.name, s.sector_id, sec.name as sector_name, s.exchange, s.currency, s.is_active
        FROM symbols s
        JOIN sectors sec ON s.sector_id = sec.id
        WHERE s.ticker = ?
        """,
        (ticker.upper(),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "ticker": row[1],
        "name": row[2],
        "sector_id": row[3],
        "sector_name": row[4],
        "exchange": row[5],
        "currency": row[6],
        "is_active": bool(row[7]),
    }


def get_symbols_by_sector(
    conn: sqlite3.Connection, sector_name: str
) -> list[dict[str, Any]]:
    """Query all symbols belonging to a sector."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.id, s.ticker, s.name, s.sector_id, sec.name as sector_name, s.exchange, s.currency, s.is_active
        FROM symbols s
        JOIN sectors sec ON s.sector_id = sec.id
        WHERE sec.name = ?
        ORDER BY s.ticker ASC
        """,
        (sector_name,),
    )
    rows = cursor.fetchall()
    return [
        {
            "id": r[0],
            "ticker": r[1],
            "name": r[2],
            "sector_id": r[3],
            "sector_name": r[4],
            "exchange": r[5],
            "currency": r[6],
            "is_active": bool(r[7]),
        }
        for r in rows
    ]


def get_daily_prices(
    conn: sqlite3.Connection,
    symbol_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch chronological daily price series for a symbol."""
    cursor = conn.cursor()
    query = "SELECT price_date, open, high, low, close, volume, adj_close FROM daily_prices WHERE symbol_id = ?"
    params: list[Any] = [symbol_id]

    if start_date:
        query += " AND price_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND price_date <= ?"
        params.append(end_date)

    query += " ORDER BY price_date ASC"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    return [
        {
            "price_date": r[0],
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
            "volume": r[5],
            "adj_close": r[6],
        }
        for r in rows
    ]
