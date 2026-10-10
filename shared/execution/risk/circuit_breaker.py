"""
PROJECT-ALPHA CircuitBreaker — emergency halts, consecutive loss tracking, and drawdown gates.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from core.config import AppConfig
from core.logging import get_logger
from core.types import BotName, RiskDecision

logger = get_logger("execution.risk.circuit_breaker")


class CircuitBreaker:
    """Monitors strategy degradation, drawdown spikes, and loss streaks."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._is_open = False
        self._emergency_stop = False
        self._reason: str | None = None
        self._tripped_at: datetime | None = None
        self._consecutive_losses: dict[str, int] = {
            BotName.STE.value: 0,
            BotName.HDA.value: 0,
            BotName.VCP.value: 0,
            BotName.BBS.value: 0,
        }
        self._current_date: str | None = None
        self._high_water_mark: float = 0.0
        self._daily_drawdown_pct: float = 0.0

    @property
    def is_open(self) -> bool:
        return self._is_open or self._emergency_stop

    @property
    def is_tripped(self) -> bool:
        return self.is_open

    @property
    def emergency_stop(self) -> bool:
        return self._emergency_stop

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def tripped_at(self) -> datetime | None:
        return self._tripped_at

    @property
    def daily_drawdown_pct(self) -> float:
        return self._daily_drawdown_pct

    def trip(self, reason: str) -> None:
        self._is_open = True
        self._reason = reason
        self._tripped_at = datetime.now(timezone.utc)
        logger.critical("Circuit breaker TRIPPED", extra={"reason": reason})

    def set_emergency_stop(
        self, enabled: bool, reason: str = "Manual Emergency Stop"
    ) -> None:
        self._emergency_stop = enabled
        if enabled:
            self._reason = reason
            self._tripped_at = datetime.now(timezone.utc)
            logger.critical("Emergency stop ACTIVATED", extra={"reason": reason})
        else:
            self._reason = None
            logger.info("Emergency stop DEACTIVATED")

    def trigger_emergency_shutdown(
        self, reason: str = "Panic Button Pressed", operator: str = "OPERATOR"
    ) -> dict[str, Any]:
        """Trigger emergency shutdown of all trading operations."""
        self.set_emergency_stop(True, reason=reason)
        return {
            "status": "HALTED",
            "reason": reason,
            "operator": operator,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def update_daily_equity(
        self, current_equity: float, date_str: str | None = None
    ) -> float:
        """Track daily equity and update high-water mark / drawdown."""
        today = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._current_date != today:
            self._current_date = today
            self._high_water_mark = current_equity
            self._daily_drawdown_pct = 0.0
            return 0.0

        if current_equity > self._high_water_mark:
            self._high_water_mark = current_equity
            self._daily_drawdown_pct = 0.0
        elif self._high_water_mark > 0:
            dd = ((self._high_water_mark - current_equity) / self._high_water_mark) * 100.0
            self._daily_drawdown_pct = dd
            max_dd = getattr(self._config, "max_drawdown_pct", 10.0) or 10.0
            if dd >= max_dd:
                self.trip(f"Daily drawdown breach: {dd:.2f}% >= {max_dd:.2f}%")

        return self._daily_drawdown_pct

    def manual_override_reset(
        self, operator: str = "ADMIN", reason: str = "Manual override", confirm: bool = False
    ) -> tuple[bool, str]:
        """Reset circuit breaker with required explicit confirmation."""
        if not confirm:
            return False, "Confirmation required: set confirm=True for explicit confirmation"
        self.reset()
        logger.info(
            "Circuit breaker manual override applied",
            extra={"operator": operator, "reason": reason},
        )
        return True, f"Circuit breaker successfully reset by {operator}"

    def reset(self) -> None:
        self._is_open = False
        self._emergency_stop = False
        self._reason = None
        self._tripped_at = None
        self._daily_drawdown_pct = 0.0
        for b in self._consecutive_losses:
            self._consecutive_losses[b] = 0
        logger.info("Circuit breaker RESET to normal operation")

    def check_breaker(self, bot: BotName, amount: float = 0.0) -> RiskDecision:
        t0 = time.perf_counter()
        if self._emergency_stop:
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_EMERGENCY_STOP",
                reason=f"Emergency stop active: {self._reason or 'Manual kill switch'}.",
                bot=bot,
                amount=amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        max_dd = getattr(self._config, "max_drawdown_pct", 10.0) or 10.0
        if self._daily_drawdown_pct >= max_dd:
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_MAX_DRAWDOWN",
                reason=f"Daily drawdown limit exceeded ({self._daily_drawdown_pct:.2f}% >= {max_dd:.2f}%).",
                bot=bot,
                amount=amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        if self._is_open:
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_CIRCUIT_BREAKER",
                reason=f"Circuit breaker is OPEN: {self._reason or 'Threshold exceeded'}.",
                bot=bot,
                amount=amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        losses = self._consecutive_losses.get(bot.value, 0)
        if losses >= self._config.max_consecutive_losses:
            self.trip(
                f"{bot.value} exceeded max consecutive losses ({losses}/{self._config.max_consecutive_losses})"
            )
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_CIRCUIT_BREAKER",
                reason=f"{bot.value} exceeded max consecutive losses ({losses}).",
                bot=bot,
                amount=amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        ms = (time.perf_counter() - t0) * 1000.0
        return RiskDecision(
            allowed=True,
            code="ALLOWED",
            reason="Circuit breaker normal.",
            bot=bot,
            amount=amount,
            adjusted_amount=amount,
            check_ms=round(ms, 2),
        )

    def record_trade_result(self, bot: BotName, pnl: float) -> None:
        key = bot.value
        if pnl < 0:
            self._consecutive_losses[key] = self._consecutive_losses.get(key, 0) + 1
            losses = self._consecutive_losses[key]
            if losses >= self._config.max_consecutive_losses:
                self.trip(f"{key} hit {losses} consecutive loss trades.")
        else:
            self._consecutive_losses[key] = 0
