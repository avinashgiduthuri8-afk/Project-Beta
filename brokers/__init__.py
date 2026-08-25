"""
Broker module exports and factory helper.
"""

from typing import Optional
from config.config_loader import BotSettings
from core.interfaces import BaseBroker
from brokers.paper_broker import PaperBroker
from brokers.zerodha_kite import ZerodhaKiteBroker
from brokers.angel_one import AngelOneBroker
from brokers.dhan import DhanBroker


def get_broker(settings: BotSettings) -> BaseBroker:
    """
    Factory function to instantiate the selected broker based on settings.
    """
    active_broker = settings.env.active_broker.upper()
    trading_mode = settings.env.trading_mode.upper()

    if trading_mode == "PAPER" or active_broker == "PAPER":
        return PaperBroker(initial_capital=settings.risk.default_capital_inr)
    elif active_broker == "ZERODHA":
        return ZerodhaKiteBroker(settings=settings)
    elif active_broker == "ANGEL_ONE":
        return AngelOneBroker(settings=settings)
    elif active_broker == "DHAN":
        return DhanBroker(settings=settings)
    else:
        # Default fallback to paper
        return PaperBroker(initial_capital=settings.risk.default_capital_inr)


__all__ = [
    "BaseBroker",
    "PaperBroker",
    "ZerodhaKiteBroker",
    "AngelOneBroker",
    "DhanBroker",
    "get_broker",
]
