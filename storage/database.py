"""
SQLite Database Storage for Project-Beta.
Maintains persistent trade logs, order lifecycle history, and daily P&L records.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from core.enums import Exchange, OrderSide, OrderStatus, OrderType, ProductType
from core.interfaces import BaseStorage
from core.models import Order, Trade

logger = logging.getLogger(__name__)


class Database(BaseStorage):
    """SQLite repository for local trade auditing and analytics."""

    def __init__(self, db_path: str = "data/project_beta.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    product TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    filled_quantity INTEGER DEFAULT 0,
                    price REAL,
                    trigger_price REAL,
                    average_price REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    status_message TEXT,
                    placed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    tag TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    side TEXT NOT NULL,
                    product TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders (order_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    date TEXT PRIMARY KEY,
                    total_trades INTEGER NOT NULL,
                    gross_pnl REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()
            logger.debug("Initialized SQLite database schema.")

    def save_order(self, order: Order) -> None:
        """Insert or replace order in SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, exchange_order_id, symbol, exchange, side, order_type,
                    product, quantity, filled_quantity, price, trigger_price,
                    average_price, status, status_message, placed_at, updated_at, tag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.order_id,
                order.exchange_order_id,
                order.symbol,
                order.exchange.value,
                order.side.value,
                order.order_type.value,
                order.product.value,
                order.quantity,
                order.filled_quantity,
                order.price,
                order.trigger_price,
                order.average_price,
                order.status.value,
                order.status_message,
                order.placed_at.isoformat(),
                order.updated_at.isoformat(),
                order.tag,
            ))
            conn.commit()

    def save_trade(self, trade: Trade) -> None:
        """Insert trade fill into SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, order_id, exchange_order_id, symbol, exchange,
                    side, product, price, quantity, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id,
                trade.order_id,
                trade.exchange_order_id,
                trade.symbol,
                trade.exchange.value,
                trade.side.value,
                trade.product.value,
                trade.price,
                trade.quantity,
                trade.timestamp.isoformat(),
            ))
            conn.commit()

    def get_trades(self, limit: int = 100) -> List[Trade]:
        """Fetch latest executed trades."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            trades = []
            for r in rows:
                trades.append(
                    Trade(
                        trade_id=r["trade_id"],
                        order_id=r["order_id"],
                        exchange_order_id=r["exchange_order_id"],
                        symbol=r["symbol"],
                        exchange=Exchange(r["exchange"]),
                        side=OrderSide(r["side"]),
                        product=ProductType(r["product"]),
                        price=r["price"],
                        quantity=r["quantity"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                    )
                )
            return trades
