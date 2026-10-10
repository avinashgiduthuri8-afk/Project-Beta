"""
V2 Quantitative Analytics Engine.

Calculates multi-horizon win rates, core quant metrics (Win Rate, Profit Factor, Max Drawdown, Net Expectancy),
risk-adjusted ratios (Sharpe, Sortino, Calmar), strategy & pair attribution matrices, and execution efficiency.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from core.logging import get_logger
from core.repository.journal_repo import JournalRepository

logger = get_logger("background.analytics.engine")


class AnalyticsEngine:
    """Quantitative Analytics & Strategy Performance Engine."""

    def __init__(self, journal_repo: JournalRepository) -> None:
        self._journal_repo = journal_repo

    async def compute_performance_metrics(
        self,
        bot_name: str | None = None,
        pair: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """
        Compute full quantitative performance metrics over recorded trade journal entries.
        """
        entries = await self._journal_repo.get_entries(
            limit=limit, offset=0, bot_name=bot_name, pair=pair
        )
        return self.calculate_metrics_from_entries(entries)

    def calculate_metrics_from_entries(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Pure calculation function processing list of trade journal dict entries into performance dict.
        """
        if not entries:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "break_even_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "gross_pnl": 0.0,
                "total_statutory_drag": 0.0,
                "net_pnl": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_inr": 0.0,
                "average_trade_duration_seconds": 0,
                "net_expectancy": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "mfe_capture_ratio": 0.0,
                "horizon_win_rates": self._empty_horizon_win_rates(),
                "strategy_attribution": {},
                "pair_attribution": {},
            }

        net_pnls = [float(e.get("net_pnl", 0.0)) for e in entries]
        gross_pnls = [float(e.get("gross_pnl", 0.0)) for e in entries]
        pnl_pcts = [float(e.get("net_pnl_pct", 0.0)) for e in entries]
        drags = [float(e.get("total_statutory_drag", 0.0)) for e in entries]
        durations = [int(e.get("duration_seconds", 0)) for e in entries]

        total_trades = len(entries)
        winning_trades = sum(1 for p in net_pnls if p > 0)
        losing_trades = sum(1 for p in net_pnls if p < 0)
        break_even_trades = total_trades - winning_trades - losing_trades

        win_rate_pct = round((winning_trades / total_trades) * 100.0, 2)

        gross_gains = sum(p for p in net_pnls if p > 0)
        gross_losses = abs(sum(p for p in net_pnls if p < 0))

        if gross_losses > 0:
            profit_factor = round(gross_gains / gross_losses, 2)
        elif gross_gains > 0:
            profit_factor = round(gross_gains, 2)
        else:
            profit_factor = 0.0

        total_gross_pnl = round(sum(gross_pnls), 2)
        total_drag = round(sum(drags), 2)
        total_net_pnl = round(sum(net_pnls), 2)

        # Drawdown calculations
        cum_pnl = np.cumsum(net_pnls)
        peak = np.maximum.accumulate(cum_pnl)
        drawdown_inr = peak - cum_pnl
        max_drawdown_inr = (
            round(float(np.max(drawdown_inr)), 2) if len(drawdown_inr) > 0 else 0.0
        )

        initial_equity = 100000.0  # Base ₹1L benchmark equity
        peak_equity = np.maximum.accumulate(initial_equity + cum_pnl)
        drawdown_pct = (peak_equity - (initial_equity + cum_pnl)) / peak_equity * 100.0
        max_drawdown_pct = (
            round(float(np.max(drawdown_pct)), 2) if len(drawdown_pct) > 0 else 0.0
        )

        avg_duration = int(np.mean(durations)) if durations else 0

        # Net Expectancy
        wins = [p for p in net_pnls if p > 0]
        losses = [abs(p) for p in net_pnls if p < 0]
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        win_rate_frac = winning_trades / total_trades
        loss_rate_frac = losing_trades / total_trades
        net_expectancy = round(
            (win_rate_frac * avg_win) - (loss_rate_frac * avg_loss), 2
        )

        # Risk-Adjusted Ratios (Sharpe, Sortino, Calmar)
        sharpe_ratio = self._calculate_sharpe_ratio(pnl_pcts)
        sortino_ratio = self._calculate_sortino_ratio(pnl_pcts)
        calmar_ratio = self._calculate_calmar_ratio(pnl_pcts, max_drawdown_pct)

        # Execution Efficiency (MFE Capture)
        mfes = [float(e.get("mfe", 0.0) or 0.0) for e in entries]
        total_mfe = sum(mfes)
        mfe_capture_ratio = (
            round((total_net_pnl / total_mfe), 2) if total_mfe > 0 else 0.0
        )

        # Multi-Horizon Win Rates
        horizon_win_rates = self._compute_horizon_win_rates(entries)

        # Strategy Attribution Matrix
        strategy_attribution = self._compute_strategy_attribution(entries)
        pair_attribution = self._compute_pair_attribution(entries)

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "break_even_trades": break_even_trades,
            "win_rate_pct": win_rate_pct,
            "profit_factor": profit_factor,
            "gross_pnl": total_gross_pnl,
            "total_statutory_drag": total_drag,
            "net_pnl": total_net_pnl,
            "max_drawdown_pct": max_drawdown_pct,
            "max_drawdown_inr": max_drawdown_inr,
            "average_trade_duration_seconds": avg_duration,
            "net_expectancy": net_expectancy,
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "calmar_ratio": calmar_ratio,
            "mfe_capture_ratio": mfe_capture_ratio,
            "horizon_win_rates": horizon_win_rates,
            "strategy_attribution": strategy_attribution,
            "pair_attribution": pair_attribution,
        }

    def _calculate_sharpe_ratio(
        self, returns_pct: list[float], annualization_factor: float = 365.0
    ) -> float:
        if not returns_pct or len(returns_pct) < 2:
            return 0.0
        arr = np.array(returns_pct)
        std = float(np.std(arr))
        if std == 0.0:
            return 0.0
        mean_ret = float(np.mean(arr))
        return round((mean_ret / std) * math.sqrt(annualization_factor), 2)

    def _calculate_sortino_ratio(
        self, returns_pct: list[float], annualization_factor: float = 365.0
    ) -> float:
        if not returns_pct or len(returns_pct) < 2:
            return 0.0
        arr = np.array(returns_pct)
        downside = arr[arr < 0]
        if len(downside) == 0:
            mean_ret = float(np.mean(arr))
            return (
                round(mean_ret * math.sqrt(annualization_factor), 2)
                if mean_ret > 0
                else 0.0
            )
        downside_std = float(np.std(downside))
        if downside_std == 0.0:
            downside_std = float(np.sqrt(np.mean(downside**2)))
        if downside_std == 0.0:
            return 0.0
        mean_ret = float(np.mean(arr))
        return round((mean_ret / downside_std) * math.sqrt(annualization_factor), 2)

    def _calculate_calmar_ratio(
        self, returns_pct: list[float], max_drawdown_pct: float
    ) -> float:
        if not returns_pct or max_drawdown_pct <= 0:
            return 0.0
        annualized_return = float(np.sum(returns_pct))
        return round(annualized_return / max_drawdown_pct, 2)

    def _empty_horizon_win_rates(self) -> dict[str, float]:
        return {
            "15m": 0.0,
            "1h": 0.0,
            "24h": 0.0,
            "7d": 0.0,
            "30d": 0.0,
            "all_time": 0.0,
        }

    def _compute_horizon_win_rates(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, float]:
        now = datetime.now(timezone.utc)
        horizons = {
            "15m": timedelta(hours=1),
            "1h": timedelta(hours=4),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
        }

        results: dict[str, float] = {}

        for h_key, delta in horizons.items():
            cutoff = now - delta
            matching = []
            for e in entries:
                try:
                    ts_str = str(e.get("exit_timestamp", ""))
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts_dt >= cutoff:
                        matching.append(e)
                except Exception:
                    pass

            if matching:
                wins = sum(1 for e in matching if float(e.get("net_pnl", 0.0)) > 0)
                results[h_key] = round((wins / len(matching)) * 100.0, 2)
            else:
                results[h_key] = 0.0

        all_wins = sum(1 for e in entries if float(e.get("net_pnl", 0.0)) > 0)
        results["all_time"] = (
            round((all_wins / len(entries)) * 100.0, 2) if entries else 0.0
        )

        return results

    def _compute_strategy_attribution(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            bot = str(e.get("bot_name", "STE")).upper()
            grouped.setdefault(bot, []).append(e)

        attribution: dict[str, dict[str, Any]] = {}
        for bot in ("STE", "HDA", "VCP", "BBS"):
            bot_entries = grouped.get(bot, [])
            if not bot_entries:
                attribution[bot] = {
                    "trades": 0,
                    "win_rate_pct": 0.0,
                    "net_pnl": 0.0,
                    "profit_factor": 0.0,
                }
            else:
                pnls = [float(e.get("net_pnl", 0.0)) for e in bot_entries]
                wins = sum(1 for p in pnls if p > 0)
                gains = sum(p for p in pnls if p > 0)
                losses = abs(sum(p for p in pnls if p < 0))
                pf = (
                    round(gains / losses, 2)
                    if losses > 0
                    else (round(gains, 2) if gains > 0 else 0.0)
                )

                attribution[bot] = {
                    "trades": len(bot_entries),
                    "win_rate_pct": round((wins / len(bot_entries)) * 100.0, 2),
                    "net_pnl": round(sum(pnls), 2),
                    "profit_factor": pf,
                }

        return attribution

    def _compute_pair_attribution(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            pair = str(e.get("pair", "BTC/INR")).upper()
            grouped.setdefault(pair, []).append(e)

        attribution: dict[str, dict[str, Any]] = {}
        for pair, p_entries in grouped.items():
            pnls = [float(e.get("net_pnl", 0.0)) for e in p_entries]
            wins = sum(1 for p in pnls if p > 0)
            attribution[pair] = {
                "trades": len(p_entries),
                "win_rate_pct": round((wins / len(p_entries)) * 100.0, 2),
                "net_pnl": round(sum(pnls), 2),
            }

        return attribution
