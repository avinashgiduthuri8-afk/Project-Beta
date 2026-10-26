from zoneinfo import ZoneInfo

import pytest

from market.metadata import (
    Currency,
    Exchange,
    IdentifierType,
    Market,
    QuantityPolicy,
    get_exchange_metadata,
)
from market.symbols import StockSymbol


@pytest.mark.parametrize(
    ("raw_symbol", "exchange"),
    [
        ("NSE:RELIANCE", Exchange.NSE),
        ("BSE:500325", Exchange.BSE),
    ],
)
def test_indian_symbols_preserve_identity_and_expose_exchange_metadata(raw_symbol, exchange):
    symbol = StockSymbol.parse(raw_symbol)

    assert symbol.exchange == exchange.value
    assert symbol.market is Market.INDIA
    assert symbol.currency is Currency.INR
    assert symbol.timezone == "Asia/Kolkata"
    assert symbol.identifier_type is IdentifierType.SECURITY_ID
    assert symbol.quantity_policy is QuantityPolicy.WHOLE_ONLY
    assert ZoneInfo(symbol.timezone).key == "Asia/Kolkata"


@pytest.mark.parametrize("exchange", [Exchange.NYSE, Exchange.NASDAQ])
def test_future_us_exchange_metadata_is_available_without_enabling_us_symbols(exchange):
    metadata = get_exchange_metadata(exchange)

    assert metadata.exchange is exchange
    assert metadata.market is Market.US
    assert metadata.currency is Currency.USD
    assert metadata.timezone == "America/New_York"
    assert metadata.identifier_type is IdentifierType.TICKER
    assert metadata.quantity_policy is QuantityPolicy.FRACTIONAL_ALLOWED
    assert ZoneInfo(metadata.timezone).key == "America/New_York"

    with pytest.raises(ValueError, match="INVALID_STOCK_SYMBOL"):
        StockSymbol.parse(f"{exchange.value}:AAPL")


def test_unknown_exchange_metadata_fails_closed():
    with pytest.raises(ValueError, match="UNSUPPORTED_STOCK_EXCHANGE"):
        get_exchange_metadata("OTC")
