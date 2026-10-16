"""Fail-closed paper-account risk thresholds for Indian cash equities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DailyRiskState:
    trading_date: date
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    charges: Decimal
    consecutive_losses: int = 0
    state: str = "ACTIVE"

    @property
    def net_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl - self.charges

    @property
    def loss(self) -> Decimal:
        return max(Decimal("0"), -self.net_pnl)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    code: str
    loss: Decimal


class PaperRiskPolicy:
    def __init__(
        self,
        warning: Decimal = Decimal("750"),
        entry_block: Decimal = Decimal("900"),
        hard_limit: Decimal = Decimal("1000"),
        max_consecutive_losses: int = 3,
    ) -> None:
        self.warning = Decimal(warning)
        self.entry_block = Decimal(entry_block)
        self.hard_limit = Decimal(hard_limit)
        self.max_consecutive_losses = int(max_consecutive_losses)

    def evaluate_daily_state(self, state: DailyRiskState) -> RiskDecision:
        loss = state.loss
        if state.state == "EMERGENCY_STOP" or loss >= self.hard_limit:
            return RiskDecision(False, "DAILY_LOSS_LIMIT_REACHED", loss)
        if state.state == "ENTRY_BLOCKED" or loss >= self.entry_block:
            return RiskDecision(False, "NEW_ENTRIES_BLOCKED", loss)
        if state.consecutive_losses >= self.max_consecutive_losses:
            return RiskDecision(False, "CONSECUTIVE_LOSS_LIMIT_REACHED", loss)
        if loss >= self.warning:
            return RiskDecision(True, "DAILY_LOSS_WARNING", loss)
        return RiskDecision(True, "ACTIVE", loss)
