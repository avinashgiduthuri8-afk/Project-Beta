"""
Multi-Timeframe OHLCV Data Feeder for PROJECT-ALPHA Backtesting Engine.

Supports CoinDCX INR pairs with mixed-value price tiers and realistic order rounding:
  - Mega-Cap / High Value: BTC/INR, ETH/INR, BNB/INR
  - Mid-Cap / Medium Value: SOL/INR, AVAX/INR, LINK/INR
  - Low-Value / Fractional: XRP/INR, ADA/INR, MATIC/INR, DOGE/INR, TRX/INR, SHIB/INR
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class PairSpec:
    pair: str
    base_price: float
    volatility: float
    price_precision: (
        int  # Decimals for price rounding (e.g. 2 for ₹100.25, 6 for ₹0.001824)
    )
    qty_precision: (
        int  # Decimals for lot quantity rounding (e.g. 5 for BTC, 0 for DOGE)
    )
    min_qty: float  # Minimum tradeable lot size
    min_notional_inr: float = (
        100.0  # Minimum order notional in INR (CoinDCX standard: ₹100)
    )


COINDCX_INR_PAIRS: dict[str, PairSpec] = {
    # ── Tier 1: Mega-Cap / High-Value Coins ──────────────────────────────────
    "BTC/INR": PairSpec(
        "BTC/INR",
        base_price=8200000.0,
        volatility=0.015,
        price_precision=2,
        qty_precision=5,
        min_qty=0.00001,
    ),
    "ETH/INR": PairSpec(
        "ETH/INR",
        base_price=260000.0,
        volatility=0.020,
        price_precision=2,
        qty_precision=4,
        min_qty=0.0001,
    ),
    "BNB/INR": PairSpec(
        "BNB/INR",
        base_price=52000.0,
        volatility=0.022,
        price_precision=1,
        qty_precision=3,
        min_qty=0.001,
    ),
    # ── Tier 2: Mid-Cap / Medium-Value Coins ─────────────────────────────────
    "SOL/INR": PairSpec(
        "SOL/INR",
        base_price=12500.0,
        volatility=0.035,
        price_precision=1,
        qty_precision=2,
        min_qty=0.01,
    ),
    "AVAX/INR": PairSpec(
        "AVAX/INR",
        base_price=2800.0,
        volatility=0.038,
        price_precision=1,
        qty_precision=2,
        min_qty=0.01,
    ),
    "LINK/INR": PairSpec(
        "LINK/INR",
        base_price=1400.0,
        volatility=0.032,
        price_precision=1,
        qty_precision=2,
        min_qty=0.01,
    ),
    # ── Tier 3: Low-Price & Fractional Coins ─────────────────────────────────
    "XRP/INR": PairSpec(
        "XRP/INR",
        base_price=110.0,
        volatility=0.030,
        price_precision=2,
        qty_precision=1,
        min_qty=0.1,
    ),
    "ADA/INR": PairSpec(
        "ADA/INR",
        base_price=65.0,
        volatility=0.035,
        price_precision=2,
        qty_precision=1,
        min_qty=0.1,
    ),
    "MATIC/INR": PairSpec(
        "MATIC/INR",
        base_price=48.0,
        volatility=0.040,
        price_precision=2,
        qty_precision=1,
        min_qty=0.1,
    ),
    "DOGE/INR": PairSpec(
        "DOGE/INR",
        base_price=16.50,
        volatility=0.050,
        price_precision=3,
        qty_precision=0,
        min_qty=1.0,
    ),
    "TRX/INR": PairSpec(
        "TRX/INR",
        base_price=18.00,
        volatility=0.028,
        price_precision=3,
        qty_precision=0,
        min_qty=1.0,
    ),
    "SHIB/INR": PairSpec(
        "SHIB/INR",
        base_price=0.0018,
        volatility=0.060,
        price_precision=6,
        qty_precision=-3,
        min_qty=1000.0,
    ),
}


# Default fallback for any unlisted pair
DEFAULT_PAIR_SPEC = PairSpec(
    "CUSTOM/INR",
    base_price=100.0,
    volatility=0.025,
    price_precision=2,
    qty_precision=2,
    min_qty=0.1,
)


def get_pair_spec(pair: str) -> PairSpec:
    """Lookup pair specification and precision parameters."""
    normalized = pair.replace("_", "/").upper()
    return COINDCX_INR_PAIRS.get(normalized, DEFAULT_PAIR_SPEC)


def round_price(pair: str, price: float) -> float:
    """Round price according to pair tick precision."""
    spec = get_pair_spec(pair)
    if spec.price_precision <= 0:
        return round(price)
    return round(price, spec.price_precision)


def round_qty(pair: str, qty: float) -> float:
    """Round lot quantity down according to pair step precision."""
    spec = get_pair_spec(pair)
    if spec.qty_precision < 0:
        # Step size is greater than 1 (e.g. SHIB step size = 1000)
        step = 10 ** abs(spec.qty_precision)
        return float(math.floor(qty / step) * step)
    elif spec.qty_precision == 0:
        return float(math.floor(qty))
    else:
        factor = 10**spec.qty_precision
        return float(math.floor(qty * factor) / factor)


def validate_and_align_ohlcv(df: pd.DataFrame, timeframe: str = "1H") -> pd.DataFrame:
    """
    Validates chronological ordering, sorts by timestamp, removes duplicates,
    and cleans missing values.
    """
    if df.empty:
        return df

    # Ensure timestamp is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        if pd.api.types.is_numeric_dtype(df["timestamp"]):
            first_val = (
                df["timestamp"].dropna().iloc[0]
                if len(df["timestamp"].dropna()) > 0
                else 0
            )
            unit = "ms" if first_val > 1e11 else "s"
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Sort chronologically ascending and drop duplicate timestamps
    df = (
        df.sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"])
        .reset_index(drop=True)
    )

    # Ensure required float columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").ffill().fillna(0.0)

    # Validate price integrity
    df["high"] = np.maximum(df["high"], np.maximum(df["open"], df["close"]))
    df["low"] = np.minimum(df["low"], np.minimum(df["open"], df["close"]))

    return df


class DataFeeder:
    """Provides historical/synthetic OHLCV dataframes with technical indicators pre-computed."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._cache: dict[str, pd.DataFrame] = {}

    def load_candles_from_records(
        self,
        records: list[dict[str, Any]],
        pair: str = "BTC/INR",
        timeframe: str = "1H",
    ) -> pd.DataFrame:
        """Load, validate, and compute technical indicators from a list of candle dicts."""
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = validate_and_align_ohlcv(df, timeframe)
        df = self._add_technical_indicators(df)
        return df

    def load_candles_from_csv(
        self,
        file_path: str | Path,
        pair: str | None = None,
        timeframe: str | None = None,
    ) -> pd.DataFrame:
        """
        Load historical candles from a CSV file (supports timestamp, open, high, low, close, volume).
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"CSV candle file not found: {p}")

        df = pd.read_csv(p)
        df.columns = [c.strip().lower() for c in df.columns]

        rename_map = {
            "t": "timestamp",
            "time": "timestamp",
            "date": "timestamp",
            "datetime": "timestamp",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
            "vol": "volume",
        }
        df = df.rename(
            columns={
                k: v
                for k, v in rename_map.items()
                if k in df.columns and v not in df.columns
            }
        )

        if "pair" in df.columns and pair:
            norm_pair = pair.upper().replace("_", "/")
            df["norm_pair"] = df["pair"].astype(str).str.upper().str.replace("_", "/")
            df = df[df["norm_pair"] == norm_pair].drop(columns=["norm_pair"])

        if "timeframe" in df.columns and timeframe:
            df = df[df["timeframe"].astype(str).str.upper() == timeframe.upper()]

        df = validate_and_align_ohlcv(df, timeframe or "1H")
        df = self._add_technical_indicators(df)
        return df

    def load_candles_from_db(
        self,
        pair: str = "BTC/INR",
        timeframe: str = "1H",
        limit: int = 10000,
        start_time: int | None = None,
        end_time: int | None = None,
        db_path: str | Path | None = None,
    ) -> pd.DataFrame:
        """
        Synchronously load historical candles directly from SQLite market_candles table.
        """
        if db_path is None:
            root = Path(__file__).resolve().parents[2]
            db_path = root / "data" / "alpha_v2.db"

        p = Path(db_path)
        if not p.exists():
            return pd.DataFrame()

        norm_pair = pair.upper().replace("_", "/")
        conn = sqlite3.connect(str(p))
        try:
            clauses = ["UPPER(REPLACE(pair, '_', '/')) = ?", "timeframe = ?"]
            params: list[Any] = [norm_pair, timeframe]

            if start_time is not None:
                clauses.append("timestamp >= ?")
                params.append(start_time)
            if end_time is not None:
                clauses.append("timestamp <= ?")
                params.append(end_time)

            where_sql = " AND ".join(clauses)
            query = f"""
                SELECT pair, timeframe, timestamp, open, high, low, close, volume
                FROM market_candles
                WHERE {where_sql}
                ORDER BY timestamp ASC
                LIMIT ?
            """
            params.append(limit)
            df = pd.read_sql_query(query, conn, params=params)
        finally:
            conn.close()

        if df.empty:
            return pd.DataFrame()

        df = validate_and_align_ohlcv(df, timeframe)
        df = self._add_technical_indicators(df)
        return df

    async def load_candles_from_repo(
        self,
        candle_repo: Any,
        pair: str = "BTC/INR",
        timeframe: str = "1H",
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 10000,
    ) -> pd.DataFrame:
        """
        Asynchronously load candles using CandleRepository instance.
        """
        if hasattr(candle_repo, "get_candles_range"):
            candles = await candle_repo.get_candles_range(
                pair, timeframe, start_time, end_time, limit
            )
        else:
            candles = await candle_repo.get_recent_candles(pair, timeframe, limit)

        return self.load_candles_from_records(candles, pair=pair, timeframe=timeframe)

    def generate_ohlcv_dataframe(
        self,
        pair: str = "BTC/INR",
        timeframe: str = "1H",
        sessions: int = 300,
    ) -> pd.DataFrame:
        """
        Generate a multi-session OHLCV dataframe with realistic price action,
        volume spikes, and technical indicators for backtesting.
        """
        spec = get_pair_spec(pair)
        cache_key = f"{spec.pair}_{timeframe}_{sessions}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        np.random.seed(self.seed + hash(pair) % 1000)

        base_price = spec.base_price
        daily_vol = spec.volatility

        tf_minutes = {"15M": 15, "1H": 60, "4H": 240}.get(timeframe, 60)
        num_bars = sessions * (1440 // tf_minutes)  # Total bars across 250+ sessions

        start_time = datetime.now(timezone.utc) - timedelta(
            minutes=num_bars * tf_minutes
        )
        timestamps = [
            start_time + timedelta(minutes=i * tf_minutes) for i in range(num_bars)
        ]

        # Geometric Brownian Motion + Volatility Regimes (Trend, Pullback, Squeeze)
        returns = np.random.normal(
            loc=0.0001, scale=daily_vol / np.sqrt(1440 / tf_minutes), size=num_bars
        )

        # Add cyclical trend/regime shifts
        cycles = np.sin(np.linspace(0, 12 * np.pi, num_bars)) * 0.002
        returns += cycles

        prices = base_price * np.exp(np.cumsum(returns))

        # Construct High, Low, Open, Close with realistic noise
        high_noise = np.abs(np.random.normal(0, daily_vol * 0.5, num_bars)) * prices
        low_noise = np.abs(np.random.normal(0, daily_vol * 0.5, num_bars)) * prices

        opens = np.roll(prices, 1)
        opens[0] = base_price
        closes = prices
        highs = np.maximum(opens, closes) + high_noise
        lows = np.minimum(opens, closes) - low_noise

        # Format with discrete price precision
        prec = max(0, spec.price_precision)
        opens = np.round(opens, prec)
        highs = np.round(highs, prec)
        lows = np.round(lows, prec)
        closes = np.round(closes, prec)

        base_vol = 10000000.0 / base_price
        volume = base_vol * (
            1.0 + np.abs(returns) * 50 + np.random.exponential(0.5, num_bars)
        )

        df = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": np.round(volume, 2),
            }
        )

        # Pre-compute core technical indicators for strategies
        df = self._add_technical_indicators(df)

        self._cache[cache_key] = df
        return df.copy()

    def _add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add EMA 9, 21, 50, 200, MACD, RSI, ATR, Donchian, Bollinger Bands, SuperTrend, Keltner."""
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]

        # EMAs
        df["ema_9"] = closes.ewm(span=9, adjust=False).mean()
        df["ema_21"] = closes.ewm(span=21, adjust=False).mean()
        df["ema_50"] = closes.ewm(span=50, adjust=False).mean()
        df["ema_200"] = closes.ewm(span=200, adjust=False).mean()

        # Volume EMA
        df["vol_ema_20"] = df["volume"].ewm(span=20, adjust=False).mean()
        df["rvol"] = df["volume"] / (df["vol_ema_20"] + 1e-9)

        # MACD
        ema_12 = closes.ewm(span=12, adjust=False).mean()
        ema_26 = closes.ewm(span=26, adjust=False).mean()
        df["macd"] = ema_12 - ema_26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # RSI 14
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=13, adjust=False).mean()
        avg_loss = loss.ewm(com=13, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        df["rsi_14"] = 100 - (100 / (1 + rs))

        # ATR 14
        tr = np.maximum(
            highs - lows,
            np.maximum(
                (highs - closes.shift(1)).abs(),
                (lows - closes.shift(1)).abs(),
            ),
        )
        df["atr_14"] = pd.Series(tr).ewm(span=14, adjust=False).mean()

        # Donchian 20
        df["donchian_high_20"] = highs.rolling(20).max().shift(1)
        df["donchian_low_20"] = lows.rolling(20).min().shift(1)

        # Bollinger Bands (20, 2)
        sma_20 = closes.rolling(20).mean()
        std_20 = closes.rolling(20).std()
        df["bb_upper"] = sma_20 + (2.0 * std_20)
        df["bb_lower"] = sma_20 - (2.0 * std_20)
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (sma_20 + 1e-9)

        # Keltner Channels (20, 1.5 ATR)
        df["kc_upper"] = sma_20 + (1.5 * df["atr_14"])
        df["kc_lower"] = sma_20 - (1.5 * df["atr_14"])

        # VWAP
        tp = (highs + lows + closes) / 3.0
        df["vwap"] = (tp * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-9)

        # CVD (Cumulative Volume Delta estimate)
        candle_direction = np.sign(closes - df["open"])
        df["cvd"] = (candle_direction * df["volume"]).cumsum()

        # NR7 Flag (Narrow Range 7)
        candle_range = highs - lows
        df["candle_range"] = candle_range
        df["min_range_7"] = candle_range.rolling(7).min()
        df["is_nr7"] = candle_range == df["min_range_7"]

        return df
