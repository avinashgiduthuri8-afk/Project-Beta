"""
PROJECT-ALPHA Trading Service Package (Execution Engine Edition).
"""

from .adapters import (
    BaseBotAdapter,
    BBSAdapter,
    HDAAdapter,
    STEAdapter,
    StrategyAdapterFactory,
    VCPAdapter,
)
from .auto_trader import AutoTradeRouter
from .position_manager import PositionManager
from .reconciliation import ReconciliationService
from .recovery import RestartRecoveryService
from .service import TradingService

__all__ = [
    "AutoTradeRouter",
    "BBSAdapter",
    "BaseBotAdapter",
    "HDAAdapter",
    "PositionManager",
    "ReconciliationService",
    "RestartRecoveryService",
    "STEAdapter",
    "StrategyAdapterFactory",
    "TradingService",
    "VCPAdapter",
]
