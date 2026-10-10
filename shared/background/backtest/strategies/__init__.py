"""
8 Candidate Algorithmic Strategies for PROJECT-ALPHA.
"""

from __future__ import annotations

from .base import BacktestTradeSignal, BaseStrategy
from .bbs import BBSStrategy
from .hda import HDAStrategy
from .mrb import MRBStrategy
from .mtb import MTBStrategy
from .nr7 import NR7Strategy
from .ppa import PPAStrategy
from .ste import STEStrategy
from .vcp import VCPStrategy

ALL_CANDIDATE_STRATEGIES = [
    VCPStrategy(),
    NR7Strategy(),
    PPAStrategy(),
    MTBStrategy(),
    HDAStrategy(),
    STEStrategy(),
    BBSStrategy(),
    MRBStrategy(),
]

__all__ = [
    "ALL_CANDIDATE_STRATEGIES",
    "BBSStrategy",
    "BacktestTradeSignal",
    "BaseStrategy",
    "HDAStrategy",
    "MRBStrategy",
    "MTBStrategy",
    "NR7Strategy",
    "PPAStrategy",
    "STEStrategy",
    "VCPStrategy",
]
