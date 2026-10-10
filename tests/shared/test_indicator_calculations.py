import numpy as np

from scanner.research.indicators import (
    compute_atr,
    compute_bollinger,
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_rvol,
    last_valid,
)


def test_compute_ema_validation():
    prices = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
    ema = compute_ema(prices, period=5)
    assert len(ema) == len(prices)
    assert np.isnan(ema[0])
    assert not np.isnan(ema[4])
    assert ema[-1] > ema[4]

    # Edge case: Insufficient data
    short_prices = np.array([10.0, 11.0])
    ema_short = compute_ema(short_prices, period=5)
    assert all(np.isnan(ema_short))


def test_compute_rsi_validation():
    # Uptrend
    prices = np.array([10.0 + i * 2.0 for i in range(30)])
    rsi = compute_rsi(prices, period=14)
    assert last_valid(rsi) >= 90.0

    # Downtrend
    prices_down = np.array([100.0 - i * 2.0 for i in range(30)])
    rsi_down = compute_rsi(prices_down, period=14)
    assert last_valid(rsi_down) <= 10.0

    # Flat prices (zero variance)
    flat_prices = np.array([50.0] * 30)
    rsi_flat = compute_rsi(flat_prices, period=14)
    assert not np.isnan(last_valid(rsi_flat))


def test_compute_macd_validation():
    prices = np.array([100.0 + (i % 5) * 2.0 + i for i in range(40)])
    macd, signal, hist = compute_macd(prices, fast=12, slow=26, signal_period=9)
    assert len(macd) == 40
    assert len(signal) == 40
    assert len(hist) == 40


def test_compute_bollinger_validation():
    prices = np.array([100.0 + (i % 3) for i in range(30)])
    upper, mid, lower = compute_bollinger(prices, period=20, std_dev=2.0)
    assert last_valid(upper) >= last_valid(mid)
    assert last_valid(mid) >= last_valid(lower)


def test_compute_atr_validation():
    high = np.array([105.0 + i for i in range(30)])
    low = np.array([95.0 + i for i in range(30)])
    close = np.array([100.0 + i for i in range(30)])
    atr = compute_atr(high, low, close, period=14)
    assert last_valid(atr) > 0.0


def test_compute_rvol_validation():
    volume = np.array([100.0] * 20 + [500.0])
    rvol = compute_rvol(volume, period=20)
    assert rvol == 5.0

    # Insufficient data
    assert compute_rvol(np.array([10.0, 20.0]), period=20) == 1.0
