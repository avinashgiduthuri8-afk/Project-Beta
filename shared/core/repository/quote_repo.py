"""Latest-quote persistence owned by Scanner and read by Paper."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import aiosqlite

from market.models import MarketQuote
from market.symbols import StockSymbol


class QuoteRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def upsert(self, quote: MarketQuote) -> None:
        await self.conn.execute(
            """INSERT INTO beta_quotes
            (canonical_symbol,symbol,exchange,security_id,price,timestamp,source,generation,bid,ask)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_symbol) DO UPDATE SET
            price=excluded.price,timestamp=excluded.timestamp,source=excluded.source,
            generation=excluded.generation,security_id=excluded.security_id,
            bid=excluded.bid,ask=excluded.ask""",
            (quote.symbol.canonical, quote.symbol.trading_symbol, quote.symbol.exchange,
             quote.symbol.security_id, str(quote.price), quote.timestamp.isoformat(),
             quote.source, quote.generation,
             str(quote.bid) if quote.bid is not None else None,
             str(quote.ask) if quote.ask is not None else None),
        )
        await self.conn.commit()

    async def get_latest(self, canonical_symbol: str) -> MarketQuote | None:
        async with self.conn.execute(
            "SELECT * FROM beta_quotes WHERE canonical_symbol=?", (canonical_symbol,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        symbol = StockSymbol(data["exchange"], data["symbol"], data["security_id"])
        return MarketQuote(
            symbol,
            Decimal(data["price"]),
            datetime.fromisoformat(data["timestamp"]),
            data["source"],
            data["generation"],
            Decimal(data["bid"]) if data.get("bid") is not None else None,
            Decimal(data["ask"]) if data.get("ask") is not None else None,
        )
