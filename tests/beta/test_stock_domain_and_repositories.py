from decimal import Decimal

import aiosqlite
import pytest

from core.repository.beta_store import BetaStore
from market.symbols import StockSymbol, validate_share_quantity


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("NSE:RELIANCE", "NSE:RELIANCE"),
        (" reliance.ns ", "NSE:RELIANCE"),
        ("BSE:500325", "BSE:500325"),
        ("500325.bo", "BSE:500325"),
    ],
)
def test_stock_symbol_normalization(raw, canonical):
    assert StockSymbol.parse(raw).canonical == canonical


@pytest.mark.parametrize("raw", ["", " ", "NASDAQ:AAPL", "NSE:../BAD", "NSE:RELIANCE/INR"])
def test_stock_symbol_rejects_unsupported_or_unsafe_values(raw):
    with pytest.raises(ValueError, match="INVALID_STOCK_SYMBOL"):
        StockSymbol.parse(raw)


@pytest.mark.parametrize("quantity", [0, -1, Decimal("1.5"), float("nan"), float("inf")])
def test_quantity_must_be_positive_whole_shares(quantity):
    with pytest.raises(ValueError, match="INVALID_SHARE_QUANTITY"):
        validate_share_quantity(quantity)


@pytest.mark.asyncio
async def test_signal_persistence_round_trip_uses_stock_fields():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    symbol = StockSymbol.parse("NSE:RELIANCE")

    inserted = await store.insert_signal_once(
        signal_id="sig-1",
        symbol=symbol,
        strategy="STE",
        score=91,
        price=Decimal("2950.25"),
        market_timestamp="2026-10-09T10:00:00+00:00",
        source="YAHOO",
    )

    assert inserted is True
    assert await store.insert_signal_once(
        "sig-1", symbol, "STE", 91, Decimal("2950.25"),
        "2026-10-09T10:00:00+00:00", "YAHOO"
    ) is False
    row = await store.get_signal("sig-1")
    assert row["symbol"] == "RELIANCE"
    assert row["exchange"] == "NSE"
    assert row["security_id"] is None
    await conn.close()
