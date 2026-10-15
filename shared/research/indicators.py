"""
Pure-NumPy and Pandas Technical Indicator Computations for Project Beta Indian Equities.
Provides robust calculation of EMA, RSI, ATR, Bollinger Bands, Keltner Channels,
Donchian Channels, RVOL, CVD, and NR7 without lookahead bias.
"""

from __future__ import annotations

from typing import Any
import numpy as np
import pandas as pd


def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average (Wilder multiplier = 2/(period+1))."""
    result = np.full(len(prices), np.nan)
    if len(prices) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.mean(prices[:period])
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1.0 - k)
    return result


def compute_sma(prices: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    result = np.full(len(prices), np.nan)
    if len(prices) < period:
        return result
    for i in range(period - 1, len(prices)):
        result[i] = np.mean(prices[i - period + 1 : i + 1])
    return result


def compute_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index (Wilder's smoothed RS)."""
    result = np.full(len(prices), np.nan)
    if len(prices) < period + 1:
        return result

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

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


def compute_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Average True Range using Wilder's smoothing."""
    n = len(high)
    result = np.full(n, np.nan)
    if n < 2:
        return result

    tr = np.full(n, np.nan)
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

    if n < period + 1:
        return result

    result[period] = np.mean(tr[1 : period + 1])
    k = 1.0 / period
    for i in range(period + 1, n):
        result[i] = tr[i] * k + result[i - 1] * (1.0 - k)

    return result


def compute_bollinger(
    prices: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands: returns (upper, mid, lower)."""
    n = len(prices)
    upper = np.full(n, np.nan)
    mid = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = prices[i - period + 1 : i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=1) if len(window) > 1 else 0.0
        mid[i] = m
        upper[i] = m + std_dev * s
        lower[i] = m - std_dev * s

    return upper, mid, lower


def compute_rvol(volume: np.ndarray, period: int = 20) -> np.ndarray:
    """Rolling Relative Volume (volume / rolling 20 mean)."""
    n = len(volume)
    rvol = np.full(n, 1.0)
    for i in range(period, n):
        avg = np.mean(volume[i - period : i])
        rvol[i] = round(float(volume[i] / avg), 2) if avg > 0 else 1.0
    return rvol


def enrich_candles_df(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Takes raw candle dicts [{'timestamp', 'open', 'high', 'low', 'close', 'volume'}, ...]
    and produces a pandas DataFrame enriched with all technical indicators required by
    the 5 core strategies (STE, HDA, VCP, BBS, NR7).
    """
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    n = len(df)
    if n == 0:
        return df

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values

    # Moving averages
    df["ema_21"] = compute_ema(closes, 21)
    df["ema_50"] = compute_ema(closes, 50)
    df["ema_200"] = compute_ema(closes, 200) if n >= 200 else np.full(n, np.nan)

    # Momentum & Volatility
    df["rsi_14"] = compute_rsi(closes, 14)
    df["atr_14"] = compute_atr(highs, lows, closes, 14)

    # Bollinger Bands (20, 2)
    bb_u, bb_m, bb_l = compute_bollinger(closes, 20, 2.0)
    df["bb_upper"] = bb_u
    df["bb_middle"] = bb_m
    df["bb_lower"] = bb_l

    # Keltner Channels (20, 1.5 ATR)
    sma_20 = compute_sma(closes, 20)
    df["kc_upper"] = sma_20 + (1.5 * df["atr_14"].values)
    df["kc_lower"] = sma_20 - (1.5 * df["atr_14"].values)

    # Donchian 20 High (prior 20 high excluding current bar)
    df["donchian_high_20"] = pd.Series(highs).rolling(20).max().shift(1).values

    # RVOL & CVD
    df["rvol"] = compute_rvol(volumes, 20)
    candle_dir = np.sign(closes - df["open"].values)
    df["cvd"] = (candle_dir * volumes).cumsum()

    # NR7 Flag: candle range is lowest of past 7 bars
    candle_range = highs - lows
    df["candle_range"] = candle_range
    roll_min_7 = pd.Series(candle_range).rolling(7).min().values
    df["is_nr7"] = candle_range <= (roll_min_7 + 1e-9)

    return df
