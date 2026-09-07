"""Unit tests for Step 4: The 4 Bot Archetypes (STE, HDA, VCP, BBS)."""

import pytest
import numpy as np
import pandas as pd
from v2.core.bots import STEBot, HDABot, VCPBot, BBSBot


@pytest.fixture
def sample_candle_df():
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    prices = np.linspace(100.0, 120.0, 40)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices * 0.99,
        "high": prices * 1.02,
        "low": prices * 0.98,
        "close": prices,
        "volume": [100000] * 40,
    })
    return df


def test_ste_bot(sample_candle_df):
    bot = STEBot()
    res = bot.evaluate_setup("RELIANCE", sample_candle_df)
    assert res is not None
    assert res["bot"] == "STE"
    assert res["symbol"] == "RELIANCE"
    assert res["direction"] == "BUY"
    assert res["take_profit"] > res["entry_price"]
    assert res["stop_loss"] < res["entry_price"]


def test_hda_bot(sample_candle_df):
    bot = HDABot()
    # High delivery (60%) + volume surge (3x)
    df_surge = sample_candle_df.copy()
    df_surge.loc[df_surge.index[-1], "volume"] = 300000

    res = bot.evaluate_setup("TCS", df_surge, delivery_pct=60.0)
    assert res is not None
    assert res["bot"] == "HDA"
    assert res["symbol"] == "TCS"
    assert res["confluence_score"] >= 85.0


def test_vcp_bot(sample_candle_df):
    bot = VCPBot()
    # Create contracting ranges w1 > w2 > w3 and breakout close
    df_vcp = sample_candle_df.copy()
    # Add pivot high breakout at end
    df_vcp.loc[df_vcp.index[-1], "close"] = 135.0

    res = bot.evaluate_setup("INFY", df_vcp)
    assert res is not None
    assert res["bot"] == "VCP"
    assert res["symbol"] == "INFY"


def test_bbs_bot(sample_candle_df):
    bot = BBSBot()
    # Mock tight squeeze and breakout
    df_bbs = sample_candle_df.copy()
    # Make middle period very low volatility
    df_bbs.loc[df_bbs.index[-20:-1], "high"] = 110.1
    df_bbs.loc[df_bbs.index[-20:-1], "low"] = 109.9
    df_bbs.loc[df_bbs.index[-20:-1], "close"] = 110.0
    # Breakout on last bar
    df_bbs.loc[df_bbs.index[-1], "close"] = 115.0

    res = bot.evaluate_setup("HDFCBANK", df_bbs)
    assert res is not None
    assert res["bot"] == "BBS"
    assert res["symbol"] == "HDFCBANK"

