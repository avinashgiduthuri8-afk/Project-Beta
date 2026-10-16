from datetime import datetime, timezone
from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from market.models import MarketQuote
from market.symbols import StockSymbol
from scanner.indian_service import IndianScannerService


class FakeProvider:
    def __init__(self, quote, candles):
        self.quote = quote
        self.candles = candles

    async def get_quote(self, symbol):
        return self.quote

    async def get_candles(self, symbol, timeframe, limit):
        return self.candles[-limit:]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_scanner_runs_stages_one_to_five_and_deduplicates():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:RELIANCE")
    quote = MarketQuote(symbol, Decimal("100"), now, "YAHOO", 0)
    candles = [
        {"timestamp": int(now.timestamp() * 1000) + i * 60_000, "open": 90 + i,
         "high": 92 + i, "low": 89 + i, "close": 91 + i, "volume": 1_000_000}
        for i in range(20)
    ]
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    scanner = IndianScannerService(FakeProvider(quote, candles), store, [symbol], clock=lambda: now)

    first = await scanner.poll()
    second = await scanner.poll()

    assert first["stages"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert first["qualified"] == 1
    assert second["qualified"] == 0
    assert second["duplicates"] == 1
    await conn.close()


@pytest.mark.asyncio
async def test_scanner_rejects_stale_data_with_audit_code():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:TCS")
    stale = MarketQuote(symbol, Decimal("100"), datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc), "YAHOO", 0)
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    scanner = IndianScannerService(FakeProvider(stale, []), store, [symbol], clock=lambda: now)
    result = await scanner.poll()
    assert result["rejections"] == [{"symbol": "NSE:TCS", "stage": 2, "code": "STALE_MARKET_DATA"}]
    await conn.close()


@pytest.mark.asyncio
async def test_scanner_rejects_price_above_ceiling():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:RELIANCE")
    expensive = MarketQuote(symbol, Decimal("350.0"), now, "YAHOO", 0)
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    scanner = IndianScannerService(FakeProvider(expensive, []), store, [symbol], clock=lambda: now, price_ceiling=Decimal("250.0"))
    result = await scanner.poll()
    assert result["rejections"] == [{"symbol": "NSE:RELIANCE", "stage": 2, "code": "PRICE_OUT_OF_BOUNDS"}]
    await conn.close()


@pytest.mark.asyncio
async def test_scanner_rejects_slow_movement_stocks():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:SLOWNESS")
    quote = MarketQuote(symbol, Decimal("100.0"), now, "YAHOO", 0)
    # Extremely narrow range candles: ATR will be ~0.1 on a 100 stock (ATR% = 0.1% < 1.5%)
    candles = [
        {"timestamp": int(now.timestamp() * 1000) + i * 60_000, "open": 100.0,
         "high": 100.05, "low": 99.95, "close": 100.0, "volume": 1_000_000}
        for i in range(20)
    ]
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    scanner = IndianScannerService(FakeProvider(quote, candles), store, [symbol], clock=lambda: now, min_atr_pct=1.5)
    result = await scanner.poll()
    assert result["rejections"] == [{"symbol": "NSE:SLOWNESS", "stage": 4, "code": "SLOW_MOVEMENT"}]
    await conn.close()


@pytest.mark.asyncio
async def test_scanner_rejects_negative_news():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:TATASTEEL")
    quote = MarketQuote(symbol, Decimal("100"), now, "YAHOO", 0)
    candles = [
        {"timestamp": int(now.timestamp() * 1000) + i * 60_000, "open": 90 + i,
         "high": 92 + i, "low": 89 + i, "close": 91 + i, "volume": 1_000_000}
        for i in range(20)
    ]
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()

    class FakeNewsService:
        async def evaluate_symbol(self, sym):
            return {"status": "HIGH_RISK", "score": -5, "headline": "SEBI Penalty inquiry"}

    scanner = IndianScannerService(
        FakeProvider(quote, candles),
        store,
        [symbol],
        clock=lambda: now,
        news_service=FakeNewsService(),
    )
    result = await scanner.poll()
    assert result["rejections"] == [{"symbol": "NSE:TATASTEEL", "stage": 4, "code": "NEGATIVE_NEWS_RISK"}]
    await conn.close()


@pytest.mark.asyncio
async def test_scanner_holds_signal_when_news_is_unknown():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:TATASTEEL")
    quote = MarketQuote(symbol, Decimal("100"), now, "YAHOO", 0)
    candles = [
        {"timestamp": int(now.timestamp() * 1000) + i * 60_000, "open": 90 + i,
         "high": 92 + i, "low": 89 + i, "close": 91 + i, "volume": 1_000_000}
        for i in range(20)
    ]
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()

    class BrokenNewsService:
        async def evaluate_symbol(self, sym):
            raise TimeoutError("news unavailable")

    scanner = IndianScannerService(
        FakeProvider(quote, candles), store, [symbol], clock=lambda: now,
        news_service=BrokenNewsService(),
    )
    result = await scanner.poll()
    assert result["qualified"] == 0
    assert result["rejections"][-1]["code"] == "NEWS_UNKNOWN"
    await conn.close()
