"""
PROJECT-ALPHA — Telegram Module.

Provides Telegram notification formatting, alert dispatching,
and authenticated operational command interfaces.
"""

from .formatters import (
    format_circuit_breaker_alert,
    format_divergence_alert,
    format_generic_alert,
    format_position_closed_alert,
    format_position_opened_alert,
    format_signal_ai_alert,
    format_telegram_bot_fleet,
    format_telegram_help,
    format_telegram_menu,
    format_telegram_pipeline_stages,
    format_telegram_portfolio,
    format_telegram_positions,
    format_telegram_risk,
    format_telegram_signals,
    format_telegram_trades,
    format_trade_approved_alert,
    format_trade_denied_alert,
)
from .service import NotificationService
from .telegram import TelegramClient
from .telegram_interface import TelegramInteractiveInterface

__all__ = [
    "NotificationService",
    "TelegramClient",
    "TelegramInteractiveInterface",
    "format_circuit_breaker_alert",
    "format_divergence_alert",
    "format_generic_alert",
    "format_position_closed_alert",
    "format_position_opened_alert",
    "format_signal_ai_alert",
    "format_telegram_bot_fleet",
    "format_telegram_help",
    "format_telegram_menu",
    "format_telegram_pipeline_stages",
    "format_telegram_portfolio",
    "format_telegram_positions",
    "format_telegram_risk",
    "format_telegram_signals",
    "format_telegram_trades",
    "format_trade_approved_alert",
    "format_trade_denied_alert",
]
