"""Unit tests for corporate action price adjustments."""

import pytest
import pandas as pd
import numpy as np
from sector_quant.db.adjustments import calculate_corporate_action_adjustments


def test_split_adjustment_2_for_1():
    # 5 days of data. Day 3 has 2-for-1 split.
    # Prior days (0, 1, 2) prices should be halved.
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "open": [200.0, 202.0, 204.0, 103.0, 105.0],
        "high": [205.0, 206.0, 207.0, 106.0, 107.0],
        "low": [198.0, 200.0, 202.0, 101.0, 104.0],
        "close": [202.0, 204.0, 206.0, 104.0, 106.0],
        "volume": [1000, 1000, 1000, 2000, 2000],
    })

    splits = [{"date": "2024-01-04", "ratio": 2.0}]
    adj_df = calculate_corporate_action_adjustments(df, splits=splits)

    # Last bar (after split) factor = 1.0
    assert adj_df.iloc[-1]["adj_factor"] == 1.0
    assert adj_df.iloc[-1]["adj_close"] == 106.0

    # First bar (before split) factor = 0.5
    assert adj_df.iloc[0]["adj_factor"] == 0.5
    assert adj_df.iloc[0]["adj_close"] == 101.0
    assert adj_df.iloc[0]["adj_open"] == 100.0


def test_cash_dividend_adjustment():
    # 5 days of data. Day 4 has cash dividend of $5.00 on stock trading at $100.
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "open": [98.0, 99.0, 100.0, 96.0, 97.0],
        "high": [101.0, 101.0, 102.0, 98.0, 99.0],
        "low": [97.0, 98.0, 99.0, 95.0, 96.0],
        "close": [100.0, 100.0, 100.0, 96.0, 98.0],
        "volume": [1000, 1000, 1000, 1000, 1000],
    })

    dividends = [{"date": "2024-01-04", "dividend": 5.0}]
    adj_df = calculate_corporate_action_adjustments(df, dividends=dividends)

    # Dividend factor = (100 - 5) / 100 = 0.95
    assert adj_df.iloc[-1]["adj_factor"] == 1.0
    assert pytest.approx(adj_df.iloc[0]["adj_factor"], rel=1e-3) == 0.95
    assert pytest.approx(adj_df.iloc[0]["adj_close"], rel=1e-3) == 95.0

