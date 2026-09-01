"""Performance and Risk Analytics for quantitative trading backtests."""

from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class PerformanceMetrics:
    """Calculates standardized quantitative performance metrics from equity curves and trade logs."""

    @staticmethod
    def calculate_equity_metrics(
        equity_curve: pd.DataFrame,
        risk_free_rate: float = 0.0,
        periods_per_year: int = 252,
    ) -> Dict[str, Any]:
        """Computes risk and return metrics from an equity curve DataFrame.

        Expected columns in equity_curve: ['datetime', 'equity'] (or index is DatetimeIndex).
        """
        if equity_curve.empty or len(equity_curve) < 2:
            return {
                "total_return": 0.0,
                "cagr": 0.0,
                "annualized_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_duration_bars": 0,
                "calmar_ratio": 0.0,
                "total_bars": len(equity_curve),
            }

        df = equity_curve.copy()
        equity = df["equity"].values if "equity" in df.columns else df["total"].values

        initial_equity = equity[0]
        final_equity = equity[-1]

        total_return = (final_equity - initial_equity) / initial_equity if initial_equity > 0 else 0.0
        n_periods = len(equity)
        years = max(n_periods / periods_per_year, 1.0 / periods_per_year)

        # CAGR
        cagr = (final_equity / initial_equity) ** (1.0 / years) - 1.0 if initial_equity > 0 and final_equity > 0 else total_return

        # Daily returns
        daily_returns = np.diff(equity) / equity[:-1]
        daily_returns = daily_returns[np.isfinite(daily_returns)]

        if len(daily_returns) == 0:
            ann_vol = 0.0
            sharpe = 0.0
            sortino = 0.0
        else:
            mean_ret = np.mean(daily_returns)
            std_ret = np.std(daily_returns, ddof=1) if len(daily_returns) > 1 else 0.0
            ann_vol = std_ret * np.sqrt(periods_per_year)

            # Sharpe Ratio
            rf_daily = risk_free_rate / periods_per_year
            excess_return = mean_ret - rf_daily
            sharpe = (excess_return / std_ret) * np.sqrt(periods_per_year) if std_ret > 1e-8 else 0.0

            # Downside deviation for Sortino Ratio
            downside_returns = daily_returns[daily_returns < rf_daily] - rf_daily
            if len(downside_returns) > 0:
                downside_std = np.sqrt(np.mean(downside_returns ** 2))
                sortino = (excess_return / downside_std) * np.sqrt(periods_per_year) if downside_std > 1e-8 else 0.0
            else:
                sortino = sharpe

        # Drawdown computation
        running_max = np.maximum.accumulate(equity)
        drawdown_series = (equity - running_max) / running_max
        max_drawdown = float(np.min(drawdown_series))  # Negative number e.g. -0.15 for 15% DD

        # Max Drawdown Duration
        in_drawdown = drawdown_series < 0
        max_dd_duration = 0
        current_dd_duration = 0
        for is_dd in in_drawdown:
            if is_dd:
                current_dd_duration += 1
                if current_dd_duration > max_dd_duration:
                    max_dd_duration = current_dd_duration
            else:
                current_dd_duration = 0

        calmar = (cagr / abs(max_drawdown)) if abs(max_drawdown) > 1e-8 else 0.0

        return {
            "initial_equity": float(initial_equity),
            "final_equity": float(final_equity),
            "total_return": float(total_return),
            "total_return_pct": float(total_return * 100.0),
            "cagr": float(cagr),
            "cagr_pct": float(cagr * 100.0),
            "annualized_volatility": float(ann_vol),
            "annualized_volatility_pct": float(ann_vol * 100.0),
            "sharpe_ratio": float(sharpe),
            "sortino_ratio": float(sortino),
            "max_drawdown": float(max_drawdown),
            "max_drawdown_pct": float(max_drawdown * 100.0),
            "max_drawdown_duration_bars": int(max_dd_duration),
            "calmar_ratio": float(calmar),
            "total_bars": int(n_periods),
        }

    @staticmethod
    def calculate_trade_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculates win rate, profit factor, and average trade returns from closed trades list."""
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "net_profit": 0.0,
                "avg_trade_pnl": 0.0,
            }

        pnls = [float(t.get("pnl", 0.0)) for t in trades]
        profits = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]

        total_trades = len(pnls)
        winning_trades = len(profits)
        losing_trades = len(losses)
        win_rate = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

        gross_profit = sum(profits)
        gross_loss = sum(losses)
        net_profit = sum(pnls)

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (np.inf if gross_profit > 0 else 0.0)
        avg_trade_pnl = net_profit / total_trades if total_trades > 0 else 0.0

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate_pct": float(win_rate),
            "profit_factor": float(profit_factor),
            "gross_profit": float(gross_profit),
            "gross_loss": float(gross_loss),
            "net_profit": float(net_profit),
            "avg_trade_pnl": float(avg_trade_pnl),
        }

