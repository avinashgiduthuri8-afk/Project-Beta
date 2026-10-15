"""
Project Beta Strategy Package.
Exports the 5 active strategies for Indian Cash Equities.
"""

from .base import BaseStockStrategy, StrategySignal
from .bbs import BBSStrategy
from .hda import HDAStrategy
from .nr7 import NR7Strategy
from .registry import ACTIVE_STRATEGY_CLASSES, get_active_strategies
from .ste import STEStrategy
from .vcp import VCPStrategy

__all__ = [
    "BaseStockStrategy",
    "StrategySignal",
    "STEStrategy",
    "HDAStrategy",
    "VCPStrategy",
    "BBSStrategy",
    "NR7Strategy",
    "ACTIVE_STRATEGY_CLASSES",
    "get_active_strategies",
]
