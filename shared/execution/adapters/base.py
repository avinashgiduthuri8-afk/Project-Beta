"""
Base Bot Adapter for Trade Construction and Execution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.types import BotName, ExitReason


class BaseBotAdapter(ABC):
    """Abstract interface for bot execution strategy adapters."""

    def __init__(self, bot_name: BotName) -> None:
        self.bot_name = bot_name

    @abstractmethod
    def calculate_order(
        self,
        coin: str,
        pair: str,
        approved_amount: float,
        current_price: float,
        ai_adjustments: dict,
    ) -> dict:
        """Calculate exact entry price, quantity, stop loss, and take profit."""

    def check_exit(
        self,
        entry_price: float,
        current_price: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> tuple[ExitReason, float] | None:
        """Check whether current price triggers Take Profit or Stop Loss."""
        if take_profit is not None and current_price >= take_profit:
            return ExitReason.TAKE_PROFIT, current_price
        if stop_loss is not None and current_price <= stop_loss:
            return ExitReason.STOP_LOSS, current_price
        return None
