"""Market-data-only provider contract and cached Yahoo implementation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from .models import MarketQuote
from .dhan_feed import DhanMarketFeed
from .public_client import IndianStockPublicClient
from .symbols import StockSymbol


class MarketDataProvider(Protocol):
    async def get_quote(self, symbol: StockSymbol) -> MarketQuote: ...
    async def get_candles(self, symbol: StockSymbol, timeframe: str, limit: int) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


class YahooMarketDataProvider:
    """Bounded-TTL adapter that coalesces identical concurrent requests."""

    def __init__(self, client: IndianStockPublicClient | None = None, ttl_seconds: float = 15.0) -> None:
        self.client = client or IndianStockPublicClient()
        self.ttl = timedelta(seconds=max(0.1, ttl_seconds))
        self._quote_cache: dict[str, MarketQuote] = {}
        self._candle_cache: dict[tuple[str, str, int], tuple[datetime, list[dict[str, Any]]]] = {}
        self._locks: dict[tuple[Any, ...], asyncio.Lock] = {}

    def _lock(self, key: tuple[Any, ...]) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def get_quote(self, symbol: StockSymbol) -> MarketQuote:
        key = symbol.canonical
        now = datetime.now(timezone.utc)
        cached = self._quote_cache.get(key)
        if cached and cached.is_fresh(now, self.ttl):
            return cached
        async with self._lock(("quote", key)):
            now = datetime.now(timezone.utc)
            cached = self._quote_cache.get(key)
            if cached and cached.is_fresh(now, self.ttl):
                return cached
            raw = await self.client.get_quote(key)
            timestamp = raw.get("timestamp") or raw.get("time")
            if isinstance(timestamp, (int, float)):
                timestamp = datetime.fromtimestamp(timestamp / (1000 if timestamp > 10_000_000_000 else 1), timezone.utc)
            if not isinstance(timestamp, datetime):
                timestamp = now
            quote = MarketQuote(symbol, Decimal(str(raw["last_price"])), timestamp, "YAHOO", 0)
            self._quote_cache[key] = quote
            return quote

    async def get_candles(self, symbol: StockSymbol, timeframe: str, limit: int) -> list[dict[str, Any]]:
        key = (symbol.canonical, timeframe, int(limit))
        now = datetime.now(timezone.utc)
        cached = self._candle_cache.get(key)
        if cached and now - cached[0] <= self.ttl:
            return list(cached[1])
        async with self._lock(("candles", *key)):
            cached = self._candle_cache.get(key)
            now = datetime.now(timezone.utc)
            if cached and now - cached[0] <= self.ttl:
                return list(cached[1])
            candles = await self.client.get_candles(symbol.canonical, timeframe, limit)
            self._candle_cache[key] = (now, list(candles))
            if len(self._candle_cache) > 256:
                self._candle_cache.pop(next(iter(self._candle_cache)))
            return list(candles)

    async def close(self) -> None:
        await self.client.close()


class DhanMarketDataProvider:
    """Dhan real-time quotes with Yahoo candles; neither path can place orders."""

    def __init__(self, feed: DhanMarketFeed, candle_provider: YahooMarketDataProvider, quote_wait_seconds: float = 5.0) -> None:
        self.feed = feed
        self.candle_provider = candle_provider
        self.quote_wait_seconds = quote_wait_seconds

    async def get_quote(self, symbol: StockSymbol) -> MarketQuote:
        deadline = asyncio.get_running_loop().time() + self.quote_wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            quote = self.feed.latest(symbol)
            if quote:
                return quote
            await asyncio.sleep(0.05)
        raise TimeoutError(f"No current Dhan quote for {symbol.canonical}")

    async def get_candles(self, symbol: StockSymbol, timeframe: str, limit: int) -> list[dict[str, Any]]:
        return await self.candle_provider.get_candles(symbol, timeframe, limit)

    async def close(self) -> None:
        await self.feed.stop()
        await self.candle_provider.close()
