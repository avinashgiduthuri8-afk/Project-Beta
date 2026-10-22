"""Tests for TradingView screener, quote collection, and market data provider."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import AppConfig
from market.models import MarketQuote
from market.provider import TradingViewMarketDataProvider, YahooMarketDataProvider
from market.symbols import StockSymbol
from market.tradingview_client import TradingViewPublicClient


def test_tradingview_symbol_normalization():
    assert TradingViewPublicClient.normalize_symbol("NSE:TATASTEEL") == ("NSE", "TATASTEEL")
    assert TradingViewPublicClient.normalize_symbol("BSE:TATASTEEL") == ("BSE", "TATASTEEL")
    assert TradingViewPublicClient.normalize_symbol("TATASTEEL.NS") == ("NSE", "TATASTEEL")
    assert TradingViewPublicClient.normalize_symbol("TATASTEEL.BO") == ("BSE", "TATASTEEL")
    assert TradingViewPublicClient.normalize_symbol("TATASTEEL") == ("NSE", "TATASTEEL")

    sym = StockSymbol.parse("NSE:SBIN")
    assert TradingViewPublicClient.provider_ticker(sym) == "NSE:SBIN"
    assert TradingViewPublicClient.provider_ticker("TATASTEEL") == "NSE:TATASTEEL"


def test_config_accepts_tradingview_provider():
    cfg = AppConfig(_env_file=None, market_data_provider="TRADINGVIEW", tradingview_api_key="secret123")
    assert cfg.market_data_provider == "TRADINGVIEW"
    assert cfg.tradingview_api_key == "secret123"
    cfg.validate_v1_safety()


@pytest.mark.asyncio
async def test_tradingview_client_parse_and_get_quotes():
    client = TradingViewPublicClient(api_key="test_token")
    mock_payload = {
        "totalCount": 1,
        "data": [
            {
                "s": "NSE:TATASTEEL",
                "d": [
                    "TATASTEEL", 171.30, -0.65, 170.50, 171.85, 168.10,
                    13364848, 0.25, 48.5, 177.2, 178.6, 4.35, "Tata Steel Limited"
                ],
            }
        ],
    }

    with patch.object(client, "_post_scan", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_payload
        quotes = await client.get_quotes([StockSymbol.parse("NSE:TATASTEEL")])
        assert "NSE:TATASTEEL" in quotes
        q = quotes["NSE:TATASTEEL"]
        assert q["name"] == "TATASTEEL"
        assert q["last_price"] == Decimal("171.30")
        assert q["volume"] == 13364848
        assert q["rsi"] == 48.5
        assert q["source"] == "TRADINGVIEW"

        single_q = await client.get_quote(StockSymbol.parse("NSE:TATASTEEL"))
        assert single_q["last_price"] == Decimal("171.30")

    await client.close()


@pytest.mark.asyncio
async def test_tradingview_client_scan_market():
    client = TradingViewPublicClient()
    mock_payload = {
        "totalCount": 2,
        "data": [
            {
                "s": "NSE:PCJEWELLER",
                "d": ["PCJEWELLER", 18.35, 1.2, 18.0, 18.5, 17.8, 50000000, 0.35, 76.5, 18.1, 17.9, 0.9, "PC Jeweller"],
            },
            {
                "s": "NSE:IDEA",
                "d": ["IDEA", 11.55, -0.5, 11.6, 11.7, 11.4, 40000000, -0.55, 28.0, 11.8, 12.0, 0.4, "Vodafone Idea"],
            },
        ],
    }

    with patch.object(client, "_post_scan", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_payload
        candidates = await client.scan_market(price_floor=1.0, price_ceiling=250.0, limit=2)
        assert len(candidates) == 2
        assert candidates[0]["ticker"] == "NSE:PCJEWELLER"
        assert candidates[0]["last_price"] == Decimal("18.35")
        assert candidates[1]["ticker"] == "NSE:IDEA"

    await client.close()


@pytest.mark.asyncio
async def test_tradingview_market_data_provider():
    mock_client = MagicMock(spec=TradingViewPublicClient)
    mock_client.provider_ticker = lambda sym: f"{sym.exchange}:{sym.trading_symbol}"
    mock_client.get_quote = AsyncMock(return_value={
        "ticker": "NSE:TATASTEEL",
        "last_price": Decimal("172.50"),
        "timestamp": datetime.now(timezone.utc),
        "source": "TRADINGVIEW",
    })
    mock_client.get_quotes = AsyncMock(return_value={
        "NSE:TATASTEEL": {"last_price": Decimal("172.50")},
        "NSE:SBIN": {"last_price": Decimal("965.00")},
    })
    mock_client.close = AsyncMock()

    mock_candles = MagicMock(spec=YahooMarketDataProvider)
    mock_candles.get_candles = AsyncMock(return_value=[{"time": 1000, "close": 172.5}])
    mock_candles.get_quote = AsyncMock(return_value=MarketQuote(
        StockSymbol.parse("NSE:TATASTEEL"), Decimal("170.00"), datetime.now(timezone.utc), "YAHOO", 0
    ))
    mock_candles.close = AsyncMock()

    provider = TradingViewMarketDataProvider(client=mock_client, candle_provider=mock_candles, ttl_seconds=10.0)

    sym = StockSymbol.parse("NSE:TATASTEEL")
    quote = await provider.get_quote(sym)
    assert quote.symbol.canonical == "NSE:TATASTEEL"
    assert quote.price == Decimal("172.50")
    assert quote.source == "TRADINGVIEW"

    # Cached within TTL
    quote2 = await provider.get_quote(sym)
    assert quote2.price == Decimal("172.50")
    assert mock_client.get_quote.call_count == 1

    # Prefetch batch quotes
    await provider.prefetch_quotes([sym, StockSymbol.parse("NSE:SBIN")])
    assert mock_client.get_quotes.call_count == 1

    # Candle delegation
    candles = await provider.get_candles(sym, "15m", 10)
    assert len(candles) == 1
    assert candles[0]["close"] == 172.5

    # Fallback to candle provider if TradingView get_quote raises
    mock_client.get_quote.side_effect = RuntimeError("TradingView offline")
    fallback_sym = StockSymbol.parse("NSE:FALLBACK")
    fallback_quote = await provider.get_quote(fallback_sym)
    assert fallback_quote.source == "YAHOO"

    await provider.close()
    mock_client.close.assert_awaited_once()
    mock_candles.close.assert_awaited_once()
