"""
Verification suite for the 5 active quantitative strategies in Project Beta:
STE, HDA, VCP, BBS, and NR7 for Indian Cash Equities.
"""

from datetime import datetime, timezone
from decimal import Decimal
import aiosqlite
import numpy as np
import pandas as pd
import pytest

from core.repository.beta_store import BetaStore
from market.models import MarketQuote
from market.symbols import StockSymbol
from scanner.indian_service import IndianScannerService
from shared.research.indicators import (
    compute_atr,
    compute_bollinger,
    compute_ema,
    compute_rsi,
    compute_rvol,
    enrich_candles_df,
)
from shared.strategies.bbs import BBSStrategy
from shared.strategies.hda import HDAStrategy
from shared.strategies.nr7 import NR7Strategy
from shared.strategies.registry import ACTIVE_STRATEGY_CLASSES, get_active_strategies
from shared.strategies.ste import STEStrategy
from shared.strategies.vcp import VCPStrategy


def test_active_strategy_fleet_restricted_to_exact_five():
    """Verify strictly the 5 approved strategies are active in Project Beta."""
    strategies = get_active_strategies()
    assert set(strategies.keys()) == {"STE", "HDA", "VCP", "BBS", "NR7"}
    assert "MTB" not in strategies
    assert "PPA" not in strategies
    assert "MRB" not in strategies


def test_technical_indicators_produce_finite_values():
    """Verify indicators compute valid mathematical values without NaN propagation."""
    prices = np.linspace(100.0, 150.0, 50)
    highs = prices + 2.0
    lows = prices - 2.0
    volumes = np.full(50, 100000.0)

    ema = compute_ema(prices, 20)
    assert not np.isnan(ema[-1])
    assert ema[-1] > 100.0

    rsi = compute_rsi(prices, 14)
    assert not np.isnan(rsi[-1])
    assert 0 <= rsi[-1] <= 100

    atr = compute_atr(highs, lows, prices, 14)
    assert not np.isnan(atr[-1])
    assert atr[-1] > 0

    upper, mid, lower = compute_bollinger(prices, 20, 2.0)
    assert upper[-1] > mid[-1] > lower[-1]

    rvol = compute_rvol(volumes, 20)
    assert len(rvol) == 50
    assert rvol[-1] == 1.0


def test_ste_strategy_evaluates_and_triggers():
    """Verify STE triggers on EMA breakout + RSI momentum + ATR expansion."""
    symbol = StockSymbol.parse("NSE:RELIANCE")
    strat = STEStrategy()

    # Build 50 candles with an upward surge on bar 50
    candles = []
    base_price = 100.0
    for i in range(50):
        price = base_price + (i * 0.5)
        high = price + 1.0
        low = price - 1.0
        candles.append({"open": price - 0.2, "high": high, "low": low, "close": price, "volume": 100000})

    # Surge on last bar
    candles[-1]["close"] = base_price + 35.0
    candles[-1]["high"] = base_price + 36.0
    candles[-1]["open"] = base_price + 25.0

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is not None
    assert sig.strategy_name == "STE"
    assert sig.symbol.canonical == "NSE:RELIANCE"
    assert sig.entry_price > 0
    assert sig.stop_loss < sig.entry_price < sig.take_profit
    assert sig.score >= 85


def test_hda_strategy_evaluates_and_triggers():
    """Verify HDA triggers on CVD volume absorption + 15-bar high breakout + RVOL >= 1.65."""
    symbol = StockSymbol.parse("NSE:TCS")
    strat = HDAStrategy()

    candles = []
    base_price = 3000.0
    for i in range(50):
        candles.append({
            "open": base_price, "high": base_price + 5.0, "low": base_price - 5.0,
            "close": base_price + 1.0, "volume": 50000,
        })

    # Breakout with strong positive delta and RVOL
    candles[-1]["open"] = base_price
    candles[-1]["close"] = base_price + 30.0
    candles[-1]["high"] = base_price + 35.0
    candles[-1]["volume"] = 150000  # 3x volume

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is not None
    assert sig.strategy_name == "HDA"
    assert sig.symbol.canonical == "NSE:TCS"
    assert sig.stop_loss < sig.entry_price < sig.take_profit
    assert sig.metadata["rvol"] >= 1.65


def test_vcp_strategy_evaluates_and_triggers():
    """Verify VCP triggers on contracting waves (T1 >= T2 >= T3 with >=15% decay) + volume dry-up + Donchian breakout."""
    symbol = StockSymbol.parse("NSE:INFY")
    strat = VCPStrategy()

    candles = []
    base_price = 1500.0
    for i in range(55):
        # Create wave 1: T1 = 40 (bars 34 to 39, matching [i-20:i-14])
        # Create wave 2: T2 = 20 (bars 40 to 46, matching [i-14:i-7], decay ratio 20/40 = 0.50 <= 0.85)
        # Create wave 3: T3 = 10 (bars 47 to 53, matching [i-7:i], decay ratio 10/20 = 0.50 <= 0.85, volume dry-up to 50k)
        vol = 100000
        if 34 <= i <= 39:
            h, l = base_price + 20, base_price - 20  # range = 40
        elif 40 <= i <= 46:
            h, l = base_price + 10, base_price - 10  # range = 20
        elif 47 <= i <= 53:
            h, l = base_price + 5, base_price - 5    # range = 10
            vol = 50000                              # volume dries up in final contraction
        else:
            h, l = base_price + 12, base_price - 12
        candles.append({"open": base_price, "high": h, "low": l, "close": base_price, "volume": vol})

    # Bar 54 breaks out over 20-bar high with 2.5x volume
    candles[-1]["open"] = base_price
    candles[-1]["close"] = base_price + 25.0
    candles[-1]["high"] = base_price + 26.0
    candles[-1]["volume"] = 250000

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is not None
    assert sig.strategy_name == "VCP"
    assert sig.symbol.canonical == "NSE:INFY"
    assert sig.stop_loss < sig.entry_price < sig.take_profit
    assert sig.metadata["t2_ratio"] <= 0.85
    assert sig.metadata["t3_ratio"] <= 0.85
    assert sig.metadata["t3_vol_mean"] < 1.0


def test_vcp_strategy_rejects_flat_consolidation_without_decay():
    """Verify VCP rejects a flat horizontal channel where range does not contract by >=15%."""
    symbol = StockSymbol.parse("NSE:INFY")
    strat = VCPStrategy()

    candles = []
    base_price = 1500.0
    for i in range(55):
        # T1 = 20, T2 = 19.5, T3 = 19.0 (ratios ~0.97 > 0.85)
        if 35 <= i <= 40:
            h, l = base_price + 10.0, base_price - 10.0  # 20.0
        elif 41 <= i <= 47:
            h, l = base_price + 9.8, base_price - 9.7    # 19.5
        elif 48 <= i <= 53:
            h, l = base_price + 9.5, base_price - 9.5    # 19.0
        else:
            h, l = base_price + 10.0, base_price - 10.0
        candles.append({"open": base_price, "high": h, "low": l, "close": base_price, "volume": 50000})

    candles[-1]["open"] = base_price
    candles[-1]["close"] = base_price + 25.0
    candles[-1]["high"] = base_price + 26.0
    candles[-1]["volume"] = 250000

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is None


def test_vcp_strategy_rejects_without_pre_breakout_volume_dry_up():
    """Verify VCP rejects setups where volume remains elevated during the final contraction."""
    symbol = StockSymbol.parse("NSE:INFY")
    strat = VCPStrategy()

    candles = []
    base_price = 1500.0
    for i in range(55):
        # Contraction tightening holds: 40 -> 20 -> 10
        if 35 <= i <= 40:
            h, l = base_price + 20, base_price - 20
            vol = 100000
        elif 41 <= i <= 47:
            h, l = base_price + 10, base_price - 10
            vol = 100000
        elif 48 <= i <= 53:
            h, l = base_price + 5, base_price - 5
            vol = 180000  # Elevated volume in handle (failed dry-up)
        else:
            h, l = base_price + 15, base_price - 15
            vol = 100000
        candles.append({"open": base_price, "high": h, "low": l, "close": base_price, "volume": vol})

    candles[-1]["open"] = base_price
    candles[-1]["close"] = base_price + 25.0
    candles[-1]["high"] = base_price + 26.0
    candles[-1]["volume"] = 250000

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is None


def test_bbs_strategy_evaluates_and_triggers():
    """Verify BBS triggers on Bollinger Band squeeze inside Keltner followed by breakout."""
    symbol = StockSymbol.parse("NSE:HDFCBANK")
    strat = BBSStrategy()

    candles = []
    base_price = 1600.0
    # Create tightly compressed sideways candles for 40 bars (squeeze)
    for i in range(45):
        candles.append({
            "open": base_price, "high": base_price + 0.5, "low": base_price - 0.5,
            "close": base_price, "volume": 50000,
        })

    # Bar 44 breaks out above upper Bollinger Band with volume
    candles[-1]["open"] = base_price
    candles[-1]["close"] = base_price + 15.0
    candles[-1]["high"] = base_price + 16.0
    candles[-1]["volume"] = 120000

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is not None
    assert sig.strategy_name == "BBS"
    assert sig.symbol.canonical == "NSE:HDFCBANK"
    assert sig.stop_loss < sig.entry_price < sig.take_profit


def test_nr7_strategy_evaluates_and_triggers():
    """Verify NR7 triggers when bar is preceded by narrowest range in 7 bars and breaks high."""
    symbol = StockSymbol.parse("NSE:ICICIBANK")
    strat = NR7Strategy()

    candles = []
    base_price = 1000.0
    for i in range(35):
        # standard range = 10
        candles.append({
            "open": base_price, "high": base_price + 5.0, "low": base_price - 5.0,
            "close": base_price, "volume": 50000,
        })

    # Bar 33 (i=33) is NR7 bar: narrow range = 1.0
    candles[-2]["high"] = base_price + 0.5
    candles[-2]["low"] = base_price - 0.5
    candles[-2]["close"] = base_price

    # Bar 34 breaks above NR7 high with volume
    candles[-1]["open"] = base_price
    candles[-1]["high"] = base_price + 12.0
    candles[-1]["close"] = base_price + 11.0
    candles[-1]["volume"] = 100000

    df = enrich_candles_df(candles)
    sig = strat.evaluate(df, symbol)
    assert sig is not None
    assert sig.strategy_name == "NR7"
    assert sig.symbol.canonical == "NSE:ICICIBANK"
    assert sig.stop_loss < sig.entry_price < sig.take_profit


@pytest.mark.asyncio
async def test_scanner_evaluates_all_five_strategies():
    """Verify IndianScannerService executes all 5 strategies and records qualified signals."""
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:RELIANCE")
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()

    # Create candles that trigger STE
    candles = [
        {"timestamp": int(now.timestamp() * 1000) + i * 60_000, "open": 90 + i,
         "high": 92 + i, "low": 89 + i, "close": 91 + i, "volume": 1_000_000}
        for i in range(20)
    ]
    quote = MarketQuote(symbol, Decimal("110"), now, "YAHOO", 0)

    class DummyProvider:
        async def get_quote(self, s):
            return quote
        async def get_candles(self, s, tf, limit):
            return candles[-limit:]

    scanner = IndianScannerService(DummyProvider(), store, [symbol], clock=lambda: now)
    result = await scanner.poll()

    assert result["stages"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert result["qualified"] == 1
    assert result["signal_id"].startswith("sig-")

    # Verify signal in sqlite store
    sig_record = await store.get_signal(result["signal_id"])
    assert sig_record is not None
    assert sig_record["strategy"] in {"STE", "HDA", "VCP", "BBS", "NR7"}
    assert sig_record["status"] == "QUALIFIED"

    await conn.close()
