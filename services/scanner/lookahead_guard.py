"""
PROJECT-ALPHA — Lookahead Bias Elimination Guard & Verification Suite (Skill 01).

Guarantees temporal causality across scanner indicator pipelines and backtesting:
  1. Temporal Causality Invariant: Signal(t) must depend exclusively on Data(0..t).
  2. Future Perturbation Invariant: Modifying Data(t+1..N) must NOT alter Signal(t).
  3. Closed-Bar Slicing: Prevents leakage of unclosed/forming candle data into signal generation.
  4. Next-Bar Execution Protocol: Execution prices are verified to be next bar Open (t+1).
"""

from __future__ import annotations

import copy
from typing import Any, Callable

import pandas as pd

from core.exceptions import AlphaError
from core.logging import get_logger

logger = get_logger("scanner.lookahead_guard")


class LookaheadBiasError(AlphaError):
    """Raised when future data leakage is detected in indicators or signals."""


def enforce_closed_bar_slice(
    candles: list[dict[str, Any]],
    is_current_bar_forming: bool = True,
) -> list[dict[str, Any]]:
    """
    Slices candle series so only finalized closed candles are evaluated.
    If the last candle is currently forming/unclosed, it is safely excluded.
    """
    if not candles:
        return []
    if is_current_bar_forming and len(candles) > 1:
        return candles[:-1]
    return candles


def verify_temporal_causality(
    candles: list[dict[str, Any]],
    evaluator_fn: Callable[..., Any],
    eval_index: int,
    perturbation_pct: float = 50.0,
) -> bool:
    """
    Perturbation Test for Lookahead Bias:
      1. Evaluates target function on authentic candle sequence.
      2. Injects massive synthetic price jumps into future candles (index > eval_index).
      3. Re-evaluates function on perturbed dataset at eval_index.
      4. Asserts that output at eval_index is 100% IDENTICAL before and after future changes.

    Raises LookaheadBiasError if future changes alter past results.
    """
    if eval_index < 0 or eval_index >= len(candles):
        raise ValueError(f"eval_index {eval_index} out of bounds for candles of length {len(candles)}")

    def _call_eval(c_list: list[dict[str, Any]]) -> Any:
        try:
            return evaluator_fn(c_list, eval_index)
        except TypeError:
            res = evaluator_fn(c_list)
            if isinstance(res, (list, tuple)) and len(res) > eval_index:
                return res[eval_index]
            return res

    # 1. Baseline evaluation
    base_candles = [copy.deepcopy(c) for c in candles]
    baseline_output = _call_eval(base_candles)

    # 2. Perturb future bars (eval_index + 1 onwards)
    perturbed_full = [copy.deepcopy(c) for c in candles]
    multiplier = 1.0 + (perturbation_pct / 100.0)

    for i in range(eval_index + 1, len(perturbed_full)):
        c = perturbed_full[i]
        c["open"] = float(c.get("open", 0.0)) * multiplier
        c["high"] = float(c.get("high", 0.0)) * multiplier
        c["low"] = float(c.get("low", 0.0)) * multiplier
        c["close"] = float(c.get("close", 0.0)) * multiplier
        c["volume"] = float(c.get("volume", 0.0)) * 5.0

    perturbed_output = _call_eval(perturbed_full)

    if baseline_output != perturbed_output:
        raise LookaheadBiasError(
            f"Lookahead bias detected at index {eval_index}! "
            f"Baseline: {baseline_output} vs Perturbed: {perturbed_output}"
        )

    return True


def verify_dataframe_lookahead_causality(
    df: pd.DataFrame,
    indicator_fn: Callable[[pd.DataFrame], pd.Series | pd.DataFrame],
    eval_index: int,
) -> bool:
    """
    Pandas-native Lookahead Verification:
    Tests whether an indicator calculation leaks future dataframe rows.
    """
    if eval_index >= len(df) or eval_index < 0:
        raise ValueError(f"eval_index {eval_index} out of bounds")

    # 1. Evaluate baseline
    df_base = df.copy()
    res_base = indicator_fn(df_base)
    val_base = res_base.iloc[eval_index] if isinstance(res_base, (pd.Series, pd.DataFrame)) else res_base

    # 2. Perturb rows after eval_index
    df_perturbed = df.copy()
    future_mask = df_perturbed.index > df_perturbed.index[eval_index]
    if future_mask.any():
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df_perturbed.columns:
                df_perturbed.loc[future_mask, col] = df_perturbed.loc[future_mask, col] * 2.5

    res_perturbed = indicator_fn(df_perturbed)
    val_perturbed = res_perturbed.iloc[eval_index] if isinstance(res_perturbed, (pd.Series, pd.DataFrame)) else res_perturbed

    # Check equality (handles float/series)
    if isinstance(val_base, pd.Series):
        is_equal = val_base.equals(val_perturbed)
    else:
        is_equal = (val_base == val_perturbed) or (pd.isna(val_base) and pd.isna(val_perturbed))

    if not is_equal:
        raise LookaheadBiasError(
            f"DataFrame Lookahead bias detected at index {eval_index}! "
            f"Baseline: {val_base} != Perturbed: {val_perturbed}"
        )

    return True


def verify_next_bar_execution(
    trigger_bar_index: int,
    execution_bar_index: int,
) -> bool:
    """
    Enforces Backtest Next-Bar Execution Rule:
    Orders triggered on bar T MUST execute at or after bar T+1 (next bar Open).
    """
    if execution_bar_index <= trigger_bar_index:
        raise LookaheadBiasError(
            f"Execution Lookahead Violation: Signal triggered on bar {trigger_bar_index} "
            f"attempted execution on bar {execution_bar_index} (must be >= {trigger_bar_index + 1})"
        )
    return True
