"""
V2 Strategy Improvement & Parameter Sensitivity Optimizer.

Performs parameter surface grid searches, 70/30 walk-forward validation (in-sample calibration
vs out-of-sample verification), and benchmark comparison (Alpha/Beta vs BTC buy-and-hold).
"""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from background.backtest.historical_runner import HistoricalRunner
from core.logging import get_logger

logger = get_logger("background.backtest.optimizer")


class StrategyOptimizer:
    """Strategy Parameter Sensitivity & Walk-Forward Optimization Engine."""

    def __init__(self, runner: HistoricalRunner | None = None) -> None:
        self.runner = runner or HistoricalRunner()

    def walk_forward_split(
        self, candles: list[dict[str, Any]], train_ratio: float = 0.70
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Split historical candles into In-Sample (70% calibration) and Out-Of-Sample (30% verification).
        """
        sorted_candles = sorted(candles, key=lambda c: str(c.get("timestamp", "")))
        split_idx = int(len(sorted_candles) * train_ratio)
        in_sample = sorted_candles[:split_idx]
        out_of_sample = sorted_candles[split_idx:]
        return in_sample, out_of_sample

    def run_grid_search(
        self,
        strategy_name: str,
        pair: str,
        candles: list[dict[str, Any]],
        param_grid: dict[str, list[Any]],
    ) -> list[dict[str, Any]]:
        """
        Execute Cartesian product parameter grid search to evaluate parameter surface stability.
        """
        keys = list(param_grid.keys())
        value_combinations = list(itertools.product(*param_grid.values()))

        results: list[dict[str, Any]] = []

        for combo in value_combinations:
            params = dict(zip(keys, combo))
            run_summary, trades = self.runner.run_simulation(
                strategy_name, pair, candles, parameters=params
            )
            results.append(
                {
                    "parameters": params,
                    "win_rate": run_summary["win_rate"],
                    "profit_factor": run_summary["profit_factor"],
                    "total_trades": run_summary["total_trades"],
                    "max_drawdown": run_summary["max_drawdown"],
                    "sharpe_ratio": run_summary["sharpe_ratio"],
                    "run_summary": run_summary,
                }
            )

        # Sort results by profit factor descending
        results.sort(key=lambda r: float(r.get("profit_factor", 0.0)), reverse=True)
        logger.info(
            "Grid search evaluated %d parameter combinations for %s on %s",
            len(results),
            strategy_name,
            pair,
        )
        return results

    def run_walk_forward_validation(
        self,
        strategy_name: str,
        pair: str,
        candles: list[dict[str, Any]],
        param_grid: dict[str, list[Any]],
    ) -> dict[str, Any]:
        """
        Perform 70/30 Walk-Forward Validation:
          1. Split candles into 70% In-Sample (training) and 30% Out-Of-Sample (validation).
          2. Run grid search on In-Sample data to select best parameter set.
          3. Evaluate best parameter set on Out-Of-Sample data.
          4. Compare degradation to verify strategy generalization.
        """
        in_sample_candles, out_sample_candles = self.walk_forward_split(
            candles, train_ratio=0.70
        )

        # 1. Calibrate on In-Sample
        in_sample_grid = self.run_grid_search(
            strategy_name, pair, in_sample_candles, param_grid
        )
        best_in_sample = in_sample_grid[0] if in_sample_grid else {}
        best_params = best_in_sample.get("parameters", {})

        # 2. Verify on Out-Of-Sample
        oos_summary, oos_trades = self.runner.run_simulation(
            strategy_name, pair, out_sample_candles, parameters=best_params
        )

        wf_report = {
            "strategy_name": strategy_name,
            "pair": pair,
            "best_parameters": best_params,
            "in_sample": {
                "candle_count": len(in_sample_candles),
                "total_trades": best_in_sample.get("total_trades", 0),
                "win_rate": best_in_sample.get("win_rate", 0.0),
                "profit_factor": best_in_sample.get("profit_factor", 0.0),
                "max_drawdown": best_in_sample.get("max_drawdown", 0.0),
            },
            "out_of_sample": {
                "candle_count": len(out_sample_candles),
                "total_trades": oos_summary["total_trades"],
                "win_rate": oos_summary["win_rate"],
                "profit_factor": oos_summary["profit_factor"],
                "max_drawdown": oos_summary["max_drawdown"],
            },
            "overfitting_ratio": round(
                (
                    (oos_summary["win_rate"] / best_in_sample.get("win_rate", 1.0))
                    if best_in_sample.get("win_rate", 0) > 0
                    else 1.0
                ),
                2,
            ),
        }

        logger.info(
            "Walk-Forward validation for %s: In-Sample WinRate=%.1f%% -> Out-Of-Sample WinRate=%.1f%%",
            strategy_name,
            best_in_sample.get("win_rate", 0.0),
            oos_summary["win_rate"],
        )
        return wf_report

    def compute_benchmark_metrics(
        self, strategy_returns: list[float], benchmark_candles: list[dict[str, Any]]
    ) -> dict[str, float]:
        """
        Calculate Alpha and Beta against buy-and-hold BTC benchmark series.
        """
        if not benchmark_candles or not strategy_returns:
            return {
                "alpha": 0.0,
                "beta": 1.0,
                "benchmark_cagr": 0.0,
                "strategy_cagr": 0.0,
            }

        sorted_b = sorted(benchmark_candles, key=lambda c: str(c.get("timestamp", "")))
        first_close = float(sorted_b[0]["close"])
        last_close = float(sorted_b[-1]["close"])

        benchmark_return_pct = (
            ((last_close - first_close) / first_close) * 100.0
            if first_close > 0
            else 0.0
        )
        strat_return_pct = float(sum(strategy_returns))

        # Benchmark returns per candle
        b_closes = [float(c["close"]) for c in sorted_b]
        b_rets = (
            np.diff(b_closes) / b_closes[:-1] if len(b_closes) > 1 else np.array([0.0])
        )

        s_rets = np.array(strategy_returns) / 100.0
        min_len = min(len(s_rets), len(b_rets))

        if min_len > 1:
            cov = np.cov(s_rets[:min_len], b_rets[:min_len])
            b_var = np.var(b_rets[:min_len])
            beta = round(float(cov[0, 1] / b_var), 2) if b_var > 0 else 1.0
        else:
            beta = 1.0

        alpha = round(strat_return_pct - (beta * benchmark_return_pct), 2)

        return {
            "alpha": alpha,
            "beta": beta,
            "benchmark_cagr": round(benchmark_return_pct, 2),
            "strategy_cagr": round(strat_return_pct, 2),
        }
