"""
PROJECT-ALPHA — V2-Native Candle Cache Schema, Repository, and Lifecycle Tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.bus.event_bus import EventBus
from core.config import get_config
from core.repository.candle_repo import CandleRepository
from core.repository.event_log_repo import EventLogRepository
from core.repository.signal_repo import SignalRepository
from scanner import ScannerService


@pytest.fixture
async def sqlite_conn():
    # In-memory database for testing
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    # Apply migrations schema
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS market_candles (
            pair      TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open      REAL NOT NULL,
            high      REAL NOT NULL,
            low       REAL NOT NULL,
            close     REAL NOT NULL,
            volume    REAL NOT NULL,
            PRIMARY KEY (pair, timeframe, timestamp)
        )
    """)
    yield conn
    await conn.close()


@pytest.mark.anyio
async def test_candle_repo_upsert_and_get(sqlite_conn):
    repo = CandleRepository(sqlite_conn)

    # Format candles
    candles = [
        {
            "pair": "BTC/INR",
            "timeframe": "15m",
            "timestamp": 1700000000000 + i * 900000,
            "open": 80000.0,
            "high": 81000.0,
            "low": 79000.0,
            "close": 80500.0 + i,
            "volume": 1.2 + i,
        }
        for i in range(5)
    ]
    await repo.upsert_candles(candles)

    # Retrieve back
    loaded = await repo.get_recent_candles("BTC/INR", "15m", limit=10)
    assert len(loaded) == 5
    assert loaded[0]["timestamp"] < loaded[-1]["timestamp"]
    assert loaded[-1]["close"] == 80504.0
    assert loaded[-1]["volume"] == 5.2


@pytest.mark.anyio
async def test_scanner_service_bootstrap_insufficient(sqlite_conn):
    bus = MagicMock(spec=EventBus)
    signal_repo = MagicMock(spec=SignalRepository)
    event_log = MagicMock(spec=EventLogRepository)
    config = get_config()
    candle_repo = CandleRepository(sqlite_conn)

    service = ScannerService(bus, signal_repo, event_log, config, candle_repo)

    # Mock V1 watchlist fetch to return ["BTC"]
    service._fetch_watchlist_coins = AsyncMock(return_value=["BTC"])
    # Mock CoinDCX API fetch to return dummy candles
    dummy_candles = [
        {
            "time": 1700000000000 + i * 900000,
            "o": 80000.0,
            "h": 81000.0,
            "l": 79000.0,
            "c": 80500.0 + i,
            "v": 1.2 + i,
        }
        for i in range(120)
    ]
    service._fetch_coindcx_candles = AsyncMock(return_value=dummy_candles)

    # Trigger bootstrap
    await service.bootstrap_candles()

    # Verify they were saved to DB (insufficient was < 120, we had 0)
    db_candles = await candle_repo.get_recent_candles("BTC/INR", "15m", limit=150)
    assert len(db_candles) == 120
    assert service._fetch_coindcx_candles.call_count == 3  # 1h, 4h and 1d


@pytest.mark.anyio
async def test_scanner_service_bootstrap_sufficient(sqlite_conn):
    bus = MagicMock(spec=EventBus)
    signal_repo = MagicMock(spec=SignalRepository)
    event_log = MagicMock(spec=EventLogRepository)
    config = get_config()
    candle_repo = CandleRepository(sqlite_conn)

    # Pre-populate database with 120 candles for swing timeframes (15m, 1h, 1d)
    candles_15m = [
        {
            "pair": "BTC/INR",
            "timeframe": "15m",
            "timestamp": 1700000000000 + i * 3600000,
            "open": 80000.0,
            "high": 81000.0,
            "low": 79000.0,
            "close": 80500.0,
            "volume": 1.2,
        }
        for i in range(120)
    ]
    await candle_repo.upsert_candles(candles_15m)

    candles_1h = [
        {
            "pair": "BTC/INR",
            "timeframe": "1h",
            "timestamp": 1700000000000 + i * 14400000,
            "open": 80000.0,
            "high": 81000.0,
            "low": 79000.0,
            "close": 80500.0,
            "volume": 1.2,
        }
        for i in range(120)
    ]
    await candle_repo.upsert_candles(candles_1h)

    candles_1d = [
        {
            "pair": "BTC/INR",
            "timeframe": "1d",
            "timestamp": 1700000000000 + i * 86400000,
            "open": 80000.0,
            "high": 81000.0,
            "low": 79000.0,
            "close": 80500.0,
            "volume": 1.2,
        }
        for i in range(120)
    ]
    await candle_repo.upsert_candles(candles_1d)

    service = ScannerService(bus, signal_repo, event_log, config, candle_repo)
    service._fetch_watchlist_coins = AsyncMock(return_value=["BTC"])
    service._fetch_coindcx_candles = AsyncMock()

    await service.bootstrap_candles()

    # Make sure we did NOT hit the API because db had sufficient candles
    service._fetch_coindcx_candles.assert_not_called()
