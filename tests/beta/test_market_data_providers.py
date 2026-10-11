from datetime import datetime, timezone
from decimal import Decimal

import pytest

from market.dhan_feed import DhanMarketFeed
from market.instruments import DhanInstrumentMaster
from market.models import MarketQuote
from market.symbols import StockSymbol


@pytest.mark.asyncio
async def test_dhan_instrument_master_keeps_cash_equities_only():
    csv_text = """SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL
NSE,E,EQUITY,1333,HDFCBANK
NSE,D,OPTION,999,BANKNIFTY
BSE,E,EQUITY,500325,RELIANCE
"""
    master = DhanInstrumentMaster(fetcher=lambda: csv_text)
    await master.refresh()
    assert master.resolve(StockSymbol.parse("NSE:HDFCBANK")).security_id == "1333"
    assert master.resolve(StockSymbol.parse("BSE:RELIANCE")).security_id == "500325"
    with pytest.raises(KeyError):
        master.resolve(StockSymbol.parse("NSE:BANKNIFTY"))


@pytest.mark.asyncio
async def test_dhan_disconnect_invalidates_old_generation_quotes():
    feed = DhanMarketFeed("client", "token")
    symbol = StockSymbol("NSE", "HDFCBANK", "1333")
    now = datetime.now(timezone.utc)
    feed.accept_quote(MarketQuote(symbol, Decimal("1700"), now, "DHAN", 0))
    assert feed.latest(symbol) is not None
    feed.mark_disconnected()
    assert feed.generation == 1
    assert feed.latest(symbol) is None
    assert not hasattr(feed, "place_order")
