"""
Performance Metrics Calculator for Backtesting Suite.

Computes:
  - Total Trades
  - Win Rate (%)
  - Gross Profit Factor vs Net Profit Factor (After Friction)
  - Net Realized PnL (%)
  - Average Net Risk-to-Reward Ratio (R:R)
  - Max Drawdown (MDD %)
  - Expectancy Per Trade ($)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class PerformanceMetrics:
    strategy_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    gross_profit_factor: float
    net_profit_factor: float
    net_pnl_pct: float
    net_realized_pnl_dollars: float
    avg_net_rr: float
    max_drawdown_pct: float
    expectancy_per_trade: float
    survives_friction: bool


def calculate_trade_metrics(
    strategy_name: str,
    trades: list[dict[str, Any]],
    initial_capital: float = 10000.0,
) -> PerformanceMetrics:
    """
    Computes comprehensive mathematical backtest performance metrics.
    """
    if not trades:
        return PerformanceMetrics(
            strategy_name=strategy_name,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            gross_profit_factor=0.0,
            net_profit_factor=0.0,
            net_pnl_pct=0.0,
            net_realized_pnl_dollars=0.0,
            avg_net_rr=0.0,
            max_drawdown_pct=0.0,
            expectancy_per_trade=0.0,
            survives_friction=False,
        )

    total_trades = len(trades)
    net_pnls = [t["net_pnl"] for t in trades]
    gross_pnls = [t["gross_pnl"] for t in trades]

    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]

    gross_wins = [p for p in gross_pnls if p > 0]
    gross_losses = [p for p in gross_pnls if p <= 0]

    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate_pct = round((winning_trades / total_trades) * 100.0, 2)

    # Gross Profit Factor
    total_gross_gain = sum(gross_wins)
    total_gross_loss = abs(sum(gross_losses))
    gross_pf = (
        round(total_gross_gain / total_gross_loss, 2)
        if total_gross_loss > 0
        else (99.0 if total_gross_gain > 0 else 0.0)
    )

    # Net Profit Factor (After Friction)
    total_net_gain = sum(wins)
    total_net_loss = abs(sum(losses))
    net_pf = (
        round(total_net_gain / total_net_loss, 2)
        if total_net_loss > 0
        else (99.0 if total_net_gain > 0 else 0.0)
    )

    # Net PnL
    net_realized_pnl_dollars = round(sum(net_pnls), 2)
    net_pnl_pct = round((net_realized_pnl_dollars / initial_capital) * 100.0, 2)

    # Average Net Risk-to-Reward (R:R)
    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = abs(np.mean(losses)) if losses else 1.0
    avg_net_rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    # Max Drawdown (MDD %)
    equity_curve = [initial_capital]
    current = initial_capital
    for pnl in net_pnls:
        current += pnl
        equity_curve.append(current)

    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        peak = max(peak, val)
        dd = (peak - val) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    max_drawdown_pct = round(max_dd, 2)

    # Expectancy Per Trade ($)
    # Expectancy = (Win_Rate * Avg_Win) - (Loss_Rate * Avg_Loss)
    win_rate_decimal = winning_trades / total_trades
    loss_rate_decimal = 1.0 - win_rate_decimal
    expectancy_per_trade = round(
        (win_rate_decimal * avg_win) - (loss_rate_decimal * avg_loss), 2
    )

    survives_friction = net_realized_pnl_dollars > 0 and net_pf >= 1.0

    return PerformanceMetrics(
        strategy_name=strategy_name,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=win_rate_pct,
        gross_profit_factor=gross_pf,
        net_profit_factor=net_pf,
        net_pnl_pct=net_pnl_pct,
        net_realized_pnl_dollars=net_realized_pnl_dollars,
        avg_net_rr=avg_net_rr,
        max_drawdown_pct=max_drawdown_pct,
        expectancy_per_trade=expectancy_per_trade,
        survives_friction=survives_friction,
    )
