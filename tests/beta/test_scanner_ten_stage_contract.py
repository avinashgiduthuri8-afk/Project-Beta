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

    assert first["stages"] == [1, 2, 3, 4, 5]
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
