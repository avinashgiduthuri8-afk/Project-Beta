"""
PROJECT-ALPHA — Lookahead Bias Elimination Guard Unit Tests (Skill 01).
"""

from __future__ import annotations

import pandas as pd
import pytest

from scanner.lookahead_guard import (
    LookaheadBiasError,
    enforce_closed_bar_slice,
    verify_dataframe_lookahead_causality,
    verify_next_bar_execution,
    verify_temporal_causality,
)
from scanner.market_context import calculate_ema


def test_enforce_closed_bar_slice():
    candles = [{"close": 100 + i} for i in range(5)]
    # Dropping forming bar
    closed = enforce_closed_bar_slice(candles, is_current_bar_forming=True)
    assert len(closed) == 4
    assert closed[-1]["close"] == 103

    # Retaining all bars if not forming
    all_bars = enforce_closed_bar_slice(candles, is_current_bar_forming=False)
    assert len(all_bars) == 5


def test_causal_ema_passes_perturbation():
    candles = [
        {"timestamp": 1000 + i * 60, "open": 100.0 + i, "high": 102.0 + i, "low": 99.0 + i, "close": 101.0 + i, "volume": 10.0}
        for i in range(20)
    ]

    def evaluator(c_list, idx):
        closes = [c["close"] for c in c_list[: idx + 1]]
        ema_series = calculate_ema(closes, period=9)
        return ema_series[-1] if ema_series else 0.0

    # Test at index 10 (future bars are index 11..19)
    passed = verify_temporal_causality(candles, evaluator, eval_index=10, perturbation_pct=100.0)
    assert passed is True


def test_lookahead_leakage_fails_and_raises():
    candles = [
        {"timestamp": 1000 + i * 60, "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": 10.0}
        for i in range(20)
    ]

    # Malicious/buggy evaluator that peeks ahead at next bar's price
    def buggy_lookahead_evaluator(c_list, idx):
        if idx + 1 < len(c_list):
            return c_list[idx + 1]["close"]  # Lookahead leakage!
        return c_list[idx]["close"]

    with pytest.raises(LookaheadBiasError):
        verify_temporal_causality(
            candles,
            evaluator_fn=buggy_lookahead_evaluator,
            eval_index=5,
            perturbation_pct=100.0,
        )


def test_dataframe_causality_verification():
    dates = pd.date_range("2026-01-01", periods=30, freq="1h")
    df = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(30)],
            "high": [102.0 + i for i in range(30)],
            "low": [98.0 + i for i in range(30)],
            "close": [101.0 + i for i in range(30)],
            "volume": [1000.0 for _ in range(30)],
        },
        index=dates,
    )

    # Standard causal rolling mean
    def rolling_sma(data: pd.DataFrame):
        return data["close"].rolling(5).mean()

    assert verify_dataframe_lookahead_causality(df, rolling_sma, eval_index=15) is True

    # Non-causal centered rolling mean (peeks into future)
    def non_causal_centered(data: pd.DataFrame):
        return data["close"].rolling(5, center=True).mean()

    with pytest.raises(LookaheadBiasError):
        verify_dataframe_lookahead_causality(df, non_causal_centered, eval_index=15)


def test_next_bar_execution_invariant():
    # Valid: Trigger on bar 10, execute on bar 11
    assert verify_next_bar_execution(trigger_bar_index=10, execution_bar_index=11) is True
    assert verify_next_bar_execution(trigger_bar_index=10, execution_bar_index=12) is True

    # Violation: Execution on same bar (bar 10)
    with pytest.raises(LookaheadBiasError):
        verify_next_bar_execution(trigger_bar_index=10, execution_bar_index=10)

    # Violation: Execution on earlier bar (bar 9)
    with pytest.raises(LookaheadBiasError):
        verify_next_bar_execution(trigger_bar_index=10, execution_bar_index=9)
