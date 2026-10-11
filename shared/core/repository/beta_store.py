"""Small, explicit persistence boundary for Beta's three-service workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import aiosqlite

from market.symbols import StockSymbol


class BetaStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def initialize(self) -> None:
        await self.conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS beta_signals (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL CHECK(exchange IN ('NSE','BSE')),
                security_id TEXT,
                strategy TEXT NOT NULL,
                score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
                price TEXT NOT NULL,
                market_timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUALIFIED'
            );
            CREATE TABLE IF NOT EXISTS beta_quotes (
                canonical_symbol TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                security_id TEXT,
                price TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                generation INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS beta_paper_account (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                cash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS beta_consumed_signals (
                signal_id TEXT PRIMARY KEY,
                consumed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS beta_positions (
                id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                entry_price TEXT NOT NULL,
                charges TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN'
            );
            CREATE TABLE IF NOT EXISTS beta_journal (
                id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                strategy TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price TEXT NOT NULL,
                exit_price TEXT NOT NULL,
                net_pnl TEXT NOT NULL,
                exit_timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS beta_learning_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_id TEXT NOT NULL UNIQUE,
                strategy TEXT NOT NULL,
                diagnosis TEXT NOT NULL,
                candidate_multiplier TEXT NOT NULL,
                promoted INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await self.conn.commit()

    async def insert_signal_once(
        self,
        signal_id: str,
        symbol: StockSymbol,
        strategy: str,
        score: int,
        price: Decimal,
        market_timestamp: str,
        source: str,
    ) -> bool:
        cursor = await self.conn.execute(
            """INSERT OR IGNORE INTO beta_signals
            (id,symbol,exchange,security_id,strategy,score,price,market_timestamp,source)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (signal_id, symbol.trading_symbol, symbol.exchange, symbol.security_id,
             strategy, int(score), str(price), market_timestamp, source),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM beta_signals WHERE id=?", (signal_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None
