"""
Unit and Integration Tests for CoinDCX Public Market Data Client & Feeder Pipeline.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from scanner.market.feeder import MarketFeeder
from scanner.market.public_client import CoinDCXPublicClient, TokenBucketRateLimiter

# ── 1. Pair Formatting Tests ──────────────────────────────────────────────────


def test_pair_formatting():
    assert CoinDCXPublicClient.format_pair("BTC/INR") == "B-BTC_INR"
    assert CoinDCXPublicClient.format_pair("ETH/USDT") == "B-ETH_USDT"
    assert CoinDCXPublicClient.format_pair("BTCINR") == "B-BTC_INR"
    assert CoinDCXPublicClient.format_pair("B-SOL_INR") == "B-SOL_INR"
    assert CoinDCXPublicClient.format_pair("I-BTC_INR") == "I-BTC_INR"


# ── 2. Tickers Endpoint Parser Tests ──────────────────────────────────────────


@pytest.mark.anyio
async def test_get_tickers_parsing():
    raw_tickers = [
        {
            "market": "BTCINR",
            "last_price": "8500000.50",
            "bid": "8499000.00",
            "ask": "8501000.00",
            "high": "8600000.00",
            "low": "8300000.00",
            "volume": "12.55",
            "change_24_hour": "2.45",
            "timestamp": 1725000000,
        },
        {
            "market": "ETHINR",
            "last_price": "245000.00",
            "bid": "244900.00",
            "ask": "245100.00",
            "high": "250000.00",
            "low": "240000.00",
            "volume": "150.2",
            "change_24_hour": "-1.2",
            "timestamp": 1725000000,
        },
    ]

    client = CoinDCXPublicClient()
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = raw_tickers
        tickers = await client.get_tickers()

        assert len(tickers) == 2
        assert tickers[0]["market"] == "BTCINR"
        assert tickers[0]["last_price"] == 8500000.50
        assert tickers[0]["bid"] == 8499000.00
        assert tickers[0]["ask"] == 8501000.00
        assert tickers[1]["market"] == "ETHINR"
        assert tickers[1]["change_24_hour"] == -1.2

    await client.close()


# ── 3. Candles Endpoint Normalization & Chronological Sort Tests ──────────────


@pytest.mark.anyio
async def test_get_candles_normalization():
    # CoinDCX API returns candles, often descending or ascending
    raw_candles = [
        {
            "open": 85000,
            "high": 85500,
            "low": 84900,
            "close": 85200,
            "volume": 1.2,
            "time": 1725000900,
        },
        {
            "open": 84500,
            "high": 85100,
            "low": 84400,
            "close": 85000,
            "volume": 2.5,
            "time": 1725000000,
        },
    ]

    client = CoinDCXPublicClient()
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = raw_candles
        candles = await client.get_candles(pair="BTC/INR", interval="15m", limit=2)

        assert len(candles) == 2
        # Chronological ordering (oldest timestamp first)
        assert candles[0]["timestamp"] == 1725000000
        assert candles[0]["open"] == 84500.0
        assert candles[0]["close"] == 85000.0

        assert candles[1]["timestamp"] == 1725000900
        assert candles[1]["open"] == 85000.0
        assert candles[1]["close"] == 85200.0

    await client.close()


# ── 4. Order Book & Spread Calculation Tests ──────────────────────────────────


@pytest.mark.anyio
async def test_get_orderbook_spread_calculation():
    raw_ob = {
        "bids": {"8499000.00": "0.5", "8498000.00": "1.2"},
        "asks": {"8501000.00": "0.8", "8502000.00": "2.0"},
    }

    client = CoinDCXPublicClient()
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = raw_ob
        ob = await client.get_orderbook("BTC/INR")

        assert ob["pair"] == "BTC/INR"
        assert ob["best_bid"] == 8499000.00
        assert ob["best_ask"] == 8501000.00
        assert ob["spread"] == 2000.00
        assert ob["spread_pct"] > 0

    await client.close()


# ── 5. Rate Limiter Compliance Tests ──────────────────────────────────────────


@pytest.mark.anyio
async def test_rate_limiter_pacing():
    limiter = TokenBucketRateLimiter(max_rate_per_sec=10.0, burst=2)
    start_time = time.monotonic()

    # Consume burst
    await limiter.acquire()
    await limiter.acquire()

    # Next acquire will need token replenishment delay
    await limiter.acquire()
    elapsed = time.monotonic() - start_time
    assert elapsed >= 0.08  # Confirms delay was applied


# ── 6. Retry & Resilience on HTTP 429 & 503 Tests ─────────────────────────────


@pytest.mark.anyio
async def test_client_retry_on_transient_errors():
    client = CoinDCXPublicClient(max_retries=2)

    call_count = 0

    async def mock_request_handler(method, url, params=None):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count == 1:
            resp.status_code = 429
            return resp
        elif call_count == 2:
            resp.status_code = 503
            return resp
        else:
            resp.status_code = 200
            resp.json.return_value = [{"market": "SOLINR", "last_price": "12500"}]
            return resp

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.request = mock_request_handler
    client._client = mock_client

    tickers = await client.get_tickers()
    assert len(tickers) == 1
    assert tickers[0]["market"] == "SOLINR"
    assert call_count == 3  # Tried 429 -> 503 -> 200 Success

    await client.close()


# ── 7. MarketFeeder Ingestion & Repository Storage Tests ──────────────────────


@pytest.mark.anyio
async def test_market_feeder_ingestion(tmp_path):
    db_path = str(tmp_path / f"test_feeder_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    try:
        conn = db.connection
        candle_repo = CandleRepository(conn)
        bus = EventBus()

        client = CoinDCXPublicClient()

        # Mock client responses
        client.get_tickers = AsyncMock(
            return_value=[
                {
                    "market": "BTCINR",
                    "last_price": 8500000.0,
                    "bid": 8499000.0,
                    "ask": 8501000.0,
                    "high": 8600000.0,
                    "low": 8400000.0,
                    "volume": 10.0,
                    "change_24_hour": 1.5,
                    "timestamp": 1725000000,
                },
                {
                    "market": "ETHINR",
                    "last_price": 250000.0,
                    "bid": 249900.0,
                    "ask": 250100.0,
                    "high": 255000.0,
                    "low": 245000.0,
                    "volume": 50.0,
                    "change_24_hour": -0.5,
                    "timestamp": 1725000000,
                },
            ]
        )

        client.get_candles = AsyncMock(
            return_value=[
                {
                    "pair": "BTC/INR",
                    "timeframe": "15m",
                    "timestamp": 1725000000,
                    "open": 84000.0,
                    "high": 85500.0,
                    "low": 83900.0,
                    "close": 85000.0,
                    "volume": 5.0,
                },
                {
                    "pair": "BTC/INR",
                    "timeframe": "15m",
                    "timestamp": 1725000900,
                    "open": 85000.0,
                    "high": 85800.0,
                    "low": 84900.0,
                    "close": 85500.0,
                    "volume": 4.5,
                },
            ]
        )

        client.get_orderbook = AsyncMock(
            return_value={
                "pair": "BTC/INR",
                "best_bid": 8499000.0,
                "best_ask": 8501000.0,
                "spread": 2000.0,
                "spread_pct": 0.0235,
                "timestamp": 1725000000,
            }
        )

        events_received = []

        async def on_market_update(event_type: EventType, payload: dict):
            events_received.append((event_type, payload))

        bus.subscribe(EventType.MARKET_DATA_UPDATED, on_market_update)

        feeder = MarketFeeder(
            client=client,
            candle_repo=candle_repo,
            bus=bus,
            pairs=["BTC/INR"],
            intervals=["15m"],
            poll_interval=0.1,
        )

        # Execute single poll cycle
        summary = await feeder.poll_once()
        assert summary["updated_pairs"] == 1
        assert summary["total_candles_updated"] == 2
        assert len(events_received) == 1
        assert events_received[0][0] == EventType.MARKET_DATA_UPDATED

        # Verify cached accessors
        price = feeder.get_latest_price("BTC/INR")
        assert price == 8500000.0

        cached_candles = feeder.get_cached_candles("BTC/INR", "15m")
        assert len(cached_candles) == 2

        # Verify DB persistence
        db_candles = await candle_repo.get_recent_candles("BTC/INR", "15m", limit=10)
        assert len(db_candles) == 2
        assert db_candles[0]["open"] == 84000.0
        assert db_candles[1]["close"] == 85500.0

        # Verify orderbook fetch
        ob = await feeder.fetch_orderbook("BTC/INR")
        assert ob["best_bid"] == 8499000.0
        assert feeder.get_cached_orderbook("BTC/INR") is not None

        # Verify health report
        health = feeder.get_health()
        assert health["total_polls"] == 1
        assert health["tracked_pairs"] == 1

        await feeder.stop()
    finally:
        await db.close()
