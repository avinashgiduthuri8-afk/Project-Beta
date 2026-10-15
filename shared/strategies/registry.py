"""
Strategy Registry defining the 5 active strategies for Project Beta Indian Equities.
"""

from __future__ import annotations

from typing import Dict

from .base import BaseStockStrategy, StrategySignal
from .bbs import BBSStrategy
from .hda import HDAStrategy
from .nr7 import NR7Strategy
from .ste import STEStrategy
from .vcp import VCPStrategy

# Strictly the 5 active strategies approved for Project Beta
ACTIVE_STRATEGY_CLASSES = {
    "STE": STEStrategy,
    "HDA": HDAStrategy,
    "VCP": VCPStrategy,
    "BBS": BBSStrategy,
    "NR7": NR7Strategy,
}


def get_active_strategies() -> Dict[str, BaseStockStrategy]:
    """Instantiate and return the 5 active stock strategies."""
    return {name: cls() for name, cls in ACTIVE_STRATEGY_CLASSES.items()}
