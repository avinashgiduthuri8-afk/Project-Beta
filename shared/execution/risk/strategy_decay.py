"""
PROJECT-ALPHA — Strategy Decay Monitoring Engine (Skill 23).

Monitors quant strategy performance over rolling trade windows to detect alpha decay:
  1. Expectancy Calculation: E = (WinRate * AvgWin) - (LossRate * AvgLoss)
  2. Rolling Win Rate & Profit Factor Tracking
  3. Automated Strategy State Classification (HEALTHY, DEGRADED, DECAYED_HALTED)
  4. Automatic Strategy Gating: Halts entry signals for strategies with E < 0 or Win Rate < 35%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logging import get_logger
from core.types import BotName, RiskDecision

logger = get_logger("execution.risk.strategy_decay")


class StrategyHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DECAYED_HALTED = "DECAYED_HALTED"


@dataclass
class DecayReport:
    """Strategy performance and decay status report."""

    bot: str
    health_state: StrategyHealthState
    total_trades: int
    win_rate_pct: float
    expectancy: float
    profit_factor: float
    avg_win: float = 0.0
    avg_loss: float = 0.0
    rejection_reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class StrategyDecayMonitor:
    """
    Tracks rolling strategy health across STE, HDA, VCP, BBS bots.
    """

    def __init__(
        self,
        rolling_window_size: int = 20,
        min_sample_trades: int = 10,
        min_win_rate_pct: float = 35.0,
        min_expectancy: float = 0.0,
    ) -> None:
        self.rolling_window_size = rolling_window_size
        self.min_sample_trades = min_sample_trades
        self.min_win_rate_pct = min_win_rate_pct
        self.min_expectancy = min_expectancy

        # Rolling trades buffer per bot: list of PnL values
        self._history: dict[str, list[float]] = {
            BotName.STE.value: [],
            BotName.HDA.value: [],
            BotName.VCP.value: [],
            BotName.BBS.value: [],
        }

    def _get_bot_key(self, bot: BotName | str) -> str:
        if hasattr(bot, "value"):
            return str(bot.value)
        return str(bot).upper()

    def record_trade(self, bot: BotName | str, pnl: float) -> DecayReport:
        """
        Record a completed trade's net PnL and evaluate updated strategy health.
        """
        key = self._get_bot_key(bot)
        if key not in self._history:
            self._history[key] = []

        self._history[key].append(float(pnl))
        # Keep rolling window bounded
        if len(self._history[key]) > self.rolling_window_size:
            self._history[key] = self._history[key][-self.rolling_window_size :]

        return self.evaluate_strategy_health(key)

    def evaluate_strategy_health(self, bot: BotName | str) -> DecayReport:
        """
        Calculates rolling win rate, expectancy, and profit factor to evaluate strategy health.
        """
        key = self._get_bot_key(bot)
        trades = self._history.get(key, [])
        total = len(trades)

        if total < self.min_sample_trades:
            return DecayReport(
                bot=key,
                health_state=StrategyHealthState.HEALTHY,
                total_trades=total,
                win_rate_pct=50.0,
                expectancy=10.0,
                profit_factor=1.0,
                details={"message": f"Insufficient sample trades ({total}/{self.min_sample_trades})"},
            )

        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p < 0]
        win_count = len(wins)
        loss_count = len(losses)

        win_rate = (win_count / total) * 100.0
        loss_rate = 1.0 - (win_count / total)

        avg_win = (sum(wins) / win_count) if win_count > 0 else 0.0
        avg_loss = (sum(abs(p) for p in losses) / loss_count) if loss_count > 0 else 0.0

        expectancy = (win_rate / 100.0 * avg_win) - (loss_rate * avg_loss)

        total_gross_win = sum(wins)
        total_gross_loss = sum(abs(p) for p in losses)
        profit_factor = (
            (total_gross_win / total_gross_loss)
            if total_gross_loss > 0
            else (10.0 if total_gross_win > 0 else 1.0)
        )

        reasons: list[str] = []
        is_halted = False

        if win_rate < self.min_win_rate_pct:
            is_halted = True
            reasons.append(
                f"Strategy win rate ({win_rate:.1f}%) fell below minimum threshold ({self.min_win_rate_pct:.1f}%)"
            )

        if expectancy < self.min_expectancy:
            is_halted = True
            reasons.append(
                f"Strategy mathematical expectancy ({expectancy:.2f}) fell below zero ({self.min_expectancy:.2f})"
            )

        if is_halted:
            health_state = StrategyHealthState.DECAYED_HALTED
            logger.critical(
                "Strategy %s HALTED due to strategy decay: %s", key, "; ".join(reasons)
            )
        elif win_rate < 45.0 or expectancy < 3.0:
            health_state = StrategyHealthState.DEGRADED
            logger.warning(
                "Strategy %s DEGRADED performance: win_rate=%.1f%%, expectancy=%.2f",
                key,
                win_rate,
                expectancy,
            )
        else:
            health_state = StrategyHealthState.HEALTHY

        return DecayReport(
            bot=key,
            health_state=health_state,
            total_trades=total,
            win_rate_pct=round(win_rate, 1),
            expectancy=round(expectancy, 2),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            rejection_reasons=reasons,
        )

    def check_strategy_allowed(self, bot: BotName | str) -> RiskDecision:
        """
        Check if strategy is healthy to take new trades.
        """
        bot_enum = (
            bot
            if isinstance(bot, BotName)
            else BotName(bot.upper())
            if bot.upper() in BotName.__members__
            else BotName.STE
        )
        report = self.evaluate_strategy_health(bot)

        if report.health_state == StrategyHealthState.DECAYED_HALTED:
            return RiskDecision(
                allowed=False,
                code="BLOCKED_STRATEGY_DECAY",
                reason=f"Strategy {report.bot} is HALTED due to performance decay: {'; '.join(report.rejection_reasons)}.",
                bot=bot_enum,
                amount=0.0,
                adjusted_amount=0.0,
            )

        return RiskDecision(
            allowed=True,
            code="ALLOWED",
            reason=f"Strategy {report.bot} health is {report.health_state.value}.",
            bot=bot_enum,
            amount=0.0,
            adjusted_amount=0.0,
        )

    def reset_strategy(self, bot: BotName | str) -> None:
        """Reset history for strategy after manual recalibration."""
        key = self._get_bot_key(bot)
        self._history[key] = []
        logger.info("Reset strategy decay monitor history for bot %s", key)

