"""Pure numpy mathematical & econometric utilities for quantitative trading."""

from __future__ import annotations

from typing import Tuple, Dict, Any, List
import numpy as np


def rolling_ols(y: np.ndarray, x: np.ndarray) -> Tuple[float, float, float]:
    """Calculates Ordinary Least Squares (OLS) regression parameters Y = alpha + beta * X.

    Parameters
    ----------
    y : np.ndarray
        Dependent variable array.
    x : np.ndarray
        Independent variable array.

    Returns
    -------
    tuple of (beta, alpha, r_squared)
    """
    if len(y) != len(x) or len(y) < 2:
        return 1.0, 0.0, 0.0

    x_mean = np.mean(x)
    y_mean = np.mean(y)

    x_dev = x - x_mean
    y_dev = y - y_mean

    var_x = np.sum(x_dev ** 2)
    if var_x == 0:
        return 1.0, 0.0, 0.0

    cov_xy = np.sum(x_dev * y_dev)
    beta = cov_xy / var_x
    alpha = y_mean - beta * x_mean

    # R-squared calculation
    y_pred = alpha + beta * x
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum(y_dev ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 0.0

    return float(beta), float(alpha), float(r_squared)


def calculate_spread_and_zscore(
    y_series: np.ndarray,
    x_series: np.ndarray,
    beta: Optional[float] = None,
    alpha: Optional[float] = None,
    z_lookback: Optional[int] = None,
) -> Dict[str, Any]:
    """Computes residual spread e = Y - (alpha + beta * X) and rolling z-score.

    Parameters
    ----------
    y_series : np.ndarray
        Price history of stock Y.
    x_series : np.ndarray
        Price history of stock X.
    beta : float, optional
        Pre-calculated or fixed hedge ratio. If None, calculated via OLS.
    alpha : float, optional
        Pre-calculated intercept. If None, calculated via OLS.
    z_lookback : int, optional
        Lookback window for mean and standard deviation of spread.

    Returns
    -------
    dict with keys: 'beta', 'alpha', 'spread', 'spread_mean', 'spread_std', 'z_score', 'latest_spread'
    """
    if len(y_series) < 2 or len(x_series) < 2:
        return {
            "beta": 1.0, "alpha": 0.0, "spread": np.array([]),
            "spread_mean": 0.0, "spread_std": 1.0, "z_score": 0.0,
            "latest_spread": 0.0, "r_squared": 0.0
        }

    if beta is None or alpha is None:
        calc_beta, calc_alpha, r2 = rolling_ols(y_series, x_series)
        beta = beta if beta is not None else calc_beta
        alpha = alpha if alpha is not None else calc_alpha
    else:
        r2 = 0.0

    spread = y_series - (alpha + beta * x_series)
    window = z_lookback if z_lookback and z_lookback <= len(spread) else len(spread)
    recent_spread = spread[-window:]

    mean = np.mean(recent_spread)
    std = np.std(recent_spread, ddof=1) if len(recent_spread) > 1 else 0.0

    latest_s = spread[-1]
    z = (latest_s - mean) / std if std > 1e-8 else 0.0

    return {
        "beta": float(beta),
        "alpha": float(alpha),
        "r_squared": float(r2),
        "spread": spread,
        "spread_mean": float(mean),
        "spread_std": float(std),
        "z_score": float(z),
        "latest_spread": float(latest_s),
    }


def estimate_cointegration_half_life(spread: np.ndarray) -> float:
    """Estimates mean reversion half-life using an Ornstein-Uhlenbeck discrete regression:
    delta_s(t) = lambda * s(t-1) + const
    half_life = -ln(2) / lambda
    """
    if len(spread) < 5:
        return np.inf

    lagged = spread[:-1]
    delta = spread[1:] - lagged

    lambda_param, _, _ = rolling_ols(delta, lagged)
    if lambda_param >= 0:
        # Non-mean reverting or explosive
        return np.inf

    half_life = -np.log(2) / lambda_param
    return float(half_life)


def calculate_relative_strength(
    asset_prices: np.ndarray,
    benchmark_prices: np.ndarray,
    lookback: int = 20,
) -> float:
    """Calculates relative strength score of an asset versus a benchmark ETF over a lookback window."""
    if len(asset_prices) < lookback or len(benchmark_prices) < lookback:
        return 0.0

    asset_ret = (asset_prices[-1] / asset_prices[-lookback]) - 1.0
    bench_ret = (benchmark_prices[-1] / benchmark_prices[-lookback]) - 1.0

    return float(asset_ret - bench_ret)

