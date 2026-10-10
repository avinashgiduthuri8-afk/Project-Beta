"""
PROJECT-ALPHA — Market Data Validation Engine Unit & Integration Tests (Skill 04).
"""

from __future__ import annotations

import aiosqlite
import pytest

from core.repository.candle_repo import CandleRepository
from scanner.validation import (
    CandleValidationResult,
    MarketDataValidator,
    TIMEFRAME_INTERVAL_MS,
    ValidationStatus,
)


@pytest.fixture
def validator() -> MarketDataValidator:
    return MarketDataValidator(max_jump_ratio=3.0, allow_geometry_repair=False)


@pytest.fixture
def repairing_validator() -> MarketDataValidator:
    return MarketDataValidator(max_jump_ratio=3.0, allow_geometry_repair=True)


@pytest.fixture
async def in_memory_db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
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
    await conn.commit()
    yield conn
    await conn.close()


def test_valid_candle_passes(validator: MarketDataValidator):
    candle = {
        "pair": "BTC/INR",
        "timeframe": "15m",
        "timestamp": 1700000000000,
        "open": 80000.0,
        "high": 81000.0,
        "low": 79500.0,
        "close": 80500.0,
        "volume": 2.5,
    }
    is_valid, status, norm = validator.validate_candle(candle)
    assert is_valid is True
    assert status == ValidationStatus.VALID
    assert norm["open"] == 80000.0
    assert norm["high"] == 81000.0
    assert norm["low"] == 79500.0
    assert norm["close"] == 80500.0
    assert norm["volume"] == 2.5


def test_second_based_timestamp_converted_to_ms(validator: MarketDataValidator):
    # Timestamp in seconds (10 digits)
    candle = {
        "timestamp": 1700000000,
        "open": 100.0,
        "high": 105.0,
        "low": 98.0,
        "close": 102.0,
        "volume": 10.0,
    }
    is_valid, status, norm = validator.validate_candle(candle)
    assert is_valid is True
    assert norm["timestamp"] == 1700000000000


def test_zero_and_negative_prices_rejected(validator: MarketDataValidator):
    for bad_field in ["open", "high", "low", "close"]:
        c1 = {"timestamp": 1700000000000, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1.0}
        c1[bad_field] = 0.0
        is_valid, status, _ = validator.validate_candle(c1)
        assert is_valid is False
        assert status == ValidationStatus.INVALID_PRICE

        c2 = {"timestamp": 1700000000000, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1.0}
        c2[bad_field] = -5.0
        is_valid, status, _ = validator.validate_candle(c2)
        assert is_valid is False
        assert status == ValidationStatus.INVALID_PRICE


def test_negative_volume_rejected(validator: MarketDataValidator):
    candle = {
        "timestamp": 1700000000000,
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": -0.5,
    }
    is_valid, status, _ = validator.validate_candle(candle)
    assert is_valid is False
    assert status == ValidationStatus.NEGATIVE_VOLUME


def test_inverted_ohlc_geometry_rejected(validator: MarketDataValidator):
    # 1. High lower than Open
    c1 = {"timestamp": 1700000000000, "open": 100.0, "high": 90.0, "low": 80.0, "close": 85.0, "volume": 1.0}
    is_valid, status, _ = validator.validate_candle(c1)
    assert is_valid is False
    assert status == ValidationStatus.INVERTED_OHLC

    # 2. Low higher than Close
    c2 = {"timestamp": 1700000000000, "open": 100.0, "high": 110.0, "low": 95.0, "close": 90.0, "volume": 1.0}
    is_valid, status, _ = validator.validate_candle(c2)
    assert is_valid is False
    assert status == ValidationStatus.INVERTED_OHLC

    # 3. High lower than Low
    c3 = {"timestamp": 1700000000000, "open": 100.0, "high": 80.0, "low": 120.0, "close": 90.0, "volume": 1.0}
    is_valid, status, _ = validator.validate_candle(c3)
    assert is_valid is False
    assert status == ValidationStatus.INVERTED_OHLC


def test_geometry_repair_mode(repairing_validator: MarketDataValidator):
    # Candle with corrupted high (high < open) repaired
    candle = {
        "pair": "SOL/INR",
        "timeframe": "1h",
        "timestamp": 1700000000000,
        "open": 100.0,
        "high": 95.0,  # below open
        "low": 90.0,
        "close": 92.0,
        "volume": 5.0,
    }
    is_valid, status, norm = repairing_validator.validate_candle(candle)
    assert is_valid is True
    assert status == ValidationStatus.VALID
    assert norm["high"] == 100.0  # Repaired to max(high, open, close)


def test_series_sorting_and_deduplication(validator: MarketDataValidator):
    # Out of order timestamps with duplicate
    candles = [
        {"timestamp": 1700000900000, "open": 101.0, "high": 106.0, "low": 100.0, "close": 105.0, "volume": 2.0},
        {"timestamp": 1700000000000, "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1.0},
        {"timestamp": 1700000900000, "open": 101.0, "high": 107.0, "low": 100.0, "close": 106.0, "volume": 3.0},  # duplicate, newer
    ]
    res = validator.validate_series(candles, expected_timeframe="15m")
    assert res.is_valid is True
    assert len(res.cleaned_candles) == 2
    assert res.cleaned_candles[0]["timestamp"] == 1700000000000
    assert res.cleaned_candles[1]["timestamp"] == 1700000900000
    assert res.cleaned_candles[1]["close"] == 106.0


def test_series_gap_detection(validator: MarketDataValidator):
    step_15m = TIMEFRAME_INTERVAL_MS["15m"]  # 900,000ms
    candles = [
        {"timestamp": 1700000000000, "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1.0},
        # Missing bar at 1700000900000
        {"timestamp": 1700000000000 + 2 * step_15m, "open": 102.0, "high": 107.0, "low": 101.0, "close": 104.0, "volume": 1.5},
    ]
    res = validator.validate_series(candles, expected_timeframe="15m", pair="ETH/INR")
    assert len(res.gaps) == 1
    gap = res.gaps[0]
    assert gap["missing_bars"] == 1
    assert gap["pair"] == "ETH/INR"


def test_series_flash_jump_detection(validator: MarketDataValidator):
    candles = [
        {"timestamp": 1700000000000, "open": 100.0, "high": 105.0, "low": 99.0, "close": 100.0, "volume": 1.0},
        # 400% jump (max_jump_ratio = 3.0)
        {"timestamp": 1700000900000, "open": 500.0, "high": 550.0, "low": 490.0, "close": 500.0, "volume": 1.0},
    ]
    res = validator.validate_series(candles, expected_timeframe="15m")
    assert res.is_valid is False
    assert any(a.get("reason") == ValidationStatus.EXCESSIVE_JUMP.value for a in res.anomalies)


@pytest.mark.anyio
async def test_candle_repository_integration_filters_corrupted(in_memory_db):
    repo = CandleRepository(in_memory_db)

    mixed_candles = [
        # Valid candle 1
        {"pair": "BTC/INR", "timeframe": "15m", "timestamp": 1700000000000, "open": 80000.0, "high": 81000.0, "low": 79500.0, "close": 80500.0, "volume": 1.0},
        # Corrupted candle: negative close
        {"pair": "BTC/INR", "timeframe": "15m", "timestamp": 1700000900000, "open": 80500.0, "high": 81500.0, "low": 80000.0, "close": -100.0, "volume": 1.0},
        # Corrupted candle: inverted high < low
        {"pair": "BTC/INR", "timeframe": "15m", "timestamp": 1700001800000, "open": 80500.0, "high": 75000.0, "low": 82000.0, "close": 80800.0, "volume": 1.0},
        # Valid candle 2
        {"pair": "BTC/INR", "timeframe": "15m", "timestamp": 1700002700000, "open": 80800.0, "high": 82000.0, "low": 80400.0, "close": 81500.0, "volume": 2.0},
    ]

    await repo.upsert_candles(mixed_candles)

    # Only the 2 valid candles should be stored in SQLite
    stored = await repo.get_recent_candles("BTC/INR", "15m", limit=10)
    assert len(stored) == 2
    assert stored[0]["timestamp"] == 1700000000000
    assert stored[1]["timestamp"] == 1700002700000
