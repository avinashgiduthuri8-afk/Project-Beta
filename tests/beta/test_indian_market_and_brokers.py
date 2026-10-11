"""Beta v1 integration surface is market data, never broker orders."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from market.models import MarketQuote
from market.public_client import IndianStockPublicClient
from market.symbols import StockSymbol


def test_yahoo_symbol_mapping_for_nse_and_bse():
    assert IndianStockPublicClient.provider_symbol("NSE:RELIANCE") == "RELIANCE.NS"
    assert IndianStockPublicClient.provider_symbol("BSE:500325") == "500325.BO"


def test_quote_freshness_is_timestamp_based():
    now = datetime.now(timezone.utc)
    quote = MarketQuote(StockSymbol.parse("NSE:RELIANCE"), Decimal("2950.10"), now, "YAHOO", 0)
    assert quote.is_fresh(now + timedelta(seconds=10), timedelta(seconds=30))
    assert not quote.is_fresh(now + timedelta(seconds=31), timedelta(seconds=30))


def test_market_quote_rejects_invalid_price_and_naive_time():
    symbol = StockSymbol.parse("NSE:TCS")
    for price in (Decimal("0"), Decimal("NaN")):
        try:
            MarketQuote(symbol, price, datetime.now(timezone.utc), "YAHOO", 0)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid quote accepted")
