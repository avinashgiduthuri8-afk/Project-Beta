"""Broker factory and exports."""

from typing import Optional
from core.interfaces import BaseBroker
from brokers.paper_broker import PaperBroker
from brokers.zerodha_kite import ZerodhaKiteBroker
from brokers.angel_one import AngelOneBroker
from brokers.dhan import DhanBroker
from config.config_loader import AppConfig


def get_broker(config: AppConfig) -> BaseBroker:
    """Factory helper to instantiate the configured broker adapter."""
    broker_name = config.trading.broker.lower()

    if broker_name == "paper":
        return PaperBroker(
            initial_capital=config.risk.initial_capital,
            slippage_pct=config.risk.slippage_pct,
        )
    elif broker_name in ("zerodha", "kite"):
        return ZerodhaKiteBroker(
            api_key="",
            api_secret="",
            user_id="USER",
        )
    elif broker_name in ("angel", "angel_one"):
        return AngelOneBroker(
            api_key="",
            client_code="CLIENT",
        )
    elif broker_name == "dhan":
        return DhanBroker(
            client_id="CLIENT",
            access_token="",
        )
    else:
        # Default fallback to PaperBroker
        return PaperBroker(
            initial_capital=config.risk.initial_capital,
            slippage_pct=config.risk.slippage_pct,
        )


__all__ = [
    "BaseBroker",
    "PaperBroker",
    "ZerodhaKiteBroker",
    "AngelOneBroker",
    "DhanBroker",
    "get_broker",
]
