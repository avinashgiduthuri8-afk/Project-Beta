"""
Pure-NumPy Technical Indicator Computations for CoinResearchService.

All functions accept numpy arrays and return numpy arrays or scalar floats.
No third-party TA library required — avoids dependency conflicts with
the existing test suite.
"""

from __future__ import annotations

import numpy as np

# ── EMA ───────────────────────────────────────────────────────────────────────


def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """
    Exponential Moving Average with Wilder smoothing (multiplier = 2/(period+1)).
    Returns an array of the same length as prices; first (period-1) values are NaN.
    """
    result = np.full(len(prices), np.nan)
    if len(prices) < period:
        return result

    k = 2.0 / (period + 1)
    # Seed with simple average of first `period` values
    result[period - 1] = np.mean(prices[:period])
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1.0 - k)
    return result


# ── RSI ───────────────────────────────────────────────────────────────────────


def compute_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Relative Strength Index (Wilder's smoothed RS).
    Returns array same length as prices; first period values are NaN.
    """
    result = np.full(len(prices), np.nan)
    if len(prices) < period + 1:
        return result

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed averages
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 1e9
        result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


# ── MACD ──────────────────────────────────────────────────────────────────────


def compute_macd(
    prices: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    MACD Line, Signal Line, and Histogram.
    Returns (macd, signal, hist) — all same length as prices.
    """
    ema_fast = compute_ema(prices, fast)
    ema_slow = compute_ema(prices, slow)
    macd = ema_fast - ema_slow

    valid_idx = np.where(~np.isnan(macd))[0]
    if len(valid_idx) < signal_period:
        signal = np.full(len(macd), np.nan)
        hist = np.full(len(macd), np.nan)
        return macd, signal, hist

    first_valid = valid_idx[0]
    valid_macd = macd[first_valid:]

    valid_signal = compute_ema(valid_macd, signal_period)

    signal = np.full(len(macd), np.nan)
    signal[first_valid:] = valid_signal
    hist = macd - signal

    return macd, signal, hist


# ── Bollinger Bands ───────────────────────────────────────────────────────────


def compute_bollinger(
    prices: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bollinger Bands: (upper, mid, lower).
    Mid = SMA(period). Upper/Lower = mid ± std_dev * rolling std.
    """
    n = len(prices)
    upper = np.full(n, np.nan)
    mid = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = prices[i - period + 1 : i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=1)
        mid[i] = m
        upper[i] = m + std_dev * s
        lower[i] = m - std_dev * s

    return upper, mid, lower


# ── ATR ───────────────────────────────────────────────────────────────────────


def compute_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Average True Range.
    TR = max(H-L, |H-prev_C|, |L-prev_C|)
    ATR = Wilder EMA of TR.
    """
    n = len(high)
    tr = np.full(n, np.nan)
    result = np.full(n, np.nan)

    if n < 2:
        return result

    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

    if n < period + 1:
        return result

    # Wilder seed: simple mean of first `period` TRs
    result[period] = np.mean(tr[1 : period + 1])
    k = 1.0 / period
    for i in range(period + 1, n):
        result[i] = tr[i] * k + result[i - 1] * (1.0 - k)

    return result


# ── RVOL ─────────────────────────────────────────────────────────────────────


def compute_rvol(volume: np.ndarray, period: int = 20) -> float:
    """
    Relative Volume: latest bar's volume / average volume of previous `period` bars.
    Returns a scalar float.  Returns 1.0 if insufficient data.
    """
    if len(volume) < period + 1:
        return 1.0
    avg_vol = np.mean(volume[-(period + 1) : -1])
    if avg_vol <= 0:
        return 1.0
    return round(float(volume[-1] / avg_vol), 2)


# ── SMA ───────────────────────────────────────────────────────────────────────


def compute_sma(prices: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    result = np.full(len(prices), np.nan)
    for i in range(period - 1, len(prices)):
        result[i] = np.mean(prices[i - period + 1 : i + 1])
    return result


# ── Utility: safe last non-NaN ────────────────────────────────────────────────


def last_valid(arr: np.ndarray) -> float:
    """Return the last non-NaN value in the array, or 0.0."""
    valid = arr[~np.isnan(arr)]
    return float(valid[-1]) if len(valid) > 0 else 0.0
