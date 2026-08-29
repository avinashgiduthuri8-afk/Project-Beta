"""SQLite database repository for order history, trades, and P&L snapshots."""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any
from core.models import Order, Trade, Position


class Database:
    """Persistent SQLite database manager for trade and order auditing."""

    def __init__(self, db_path: str = "storage/trades.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        order_id TEXT PRIMARY KEY,
                        client_order_id TEXT,
                        symbol TEXT,
                        exchange TEXT,
                        side TEXT,
                        order_type TEXT,
                        product_type TEXT,
                        quantity INTEGER,
                        filled_quantity INTEGER,
                        price REAL,
                        average_price REAL,
                        trigger_price REAL,
                        status TEXT,
                        status_message TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        trade_id TEXT PRIMARY KEY,
                        order_id TEXT,
                        symbol TEXT,
                        side TEXT,
                        quantity INTEGER,
                        price REAL,
                        value REAL,
                        timestamp TEXT
                    )
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_snapshots (
                        date TEXT PRIMARY KEY,
                        realized_pnl REAL,
                        unrealized_pnl REAL,
                        total_pnl REAL,
                        total_trades INTEGER,
                        created_at TEXT
                    )
                """)
        finally:
            conn.close()

    def save_order(self, order: Order) -> None:
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO orders (
                        order_id, client_order_id, symbol, exchange, side, order_type,
                        product_type, quantity, filled_quantity, price, average_price,
                        trigger_price, status, status_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order.order_id, order.client_order_id, order.symbol, order.exchange.value,
                    order.side.value, order.order_type.value, order.product_type.value,
                    order.quantity, order.filled_quantity, order.price, order.average_price,
                    order.trigger_price, order.status.value, order.status_message,
                    order.created_at.isoformat(), order.updated_at.isoformat(),
                ))
        finally:
            conn.close()

    def save_trade(self, trade: Trade) -> None:
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO trades (
                        trade_id, order_id, symbol, side, quantity, price, value, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade.trade_id, trade.order_id, trade.symbol, trade.side.value,
                    trade.quantity, trade.price, trade.value, trade.timestamp.isoformat(),
                ))
        finally:
            conn.close()

    def save_daily_snapshot(self, date_str: str, realized_pnl: float, unrealized_pnl: float, total_trades: int) -> None:
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_snapshots (
                        date, realized_pnl, unrealized_pnl, total_pnl, total_trades, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    date_str, realized_pnl, unrealized_pnl,
                    realized_pnl + unrealized_pnl, total_trades, datetime.now().isoformat(),
                ))
        finally:
            conn.close()

