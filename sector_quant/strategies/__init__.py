"""Strategies module for sector quantitative trading framework."""

from sector_quant.strategies.base import Strategy
from sector_quant.strategies.math_utils import (
    rolling_ols,
    calculate_spread_and_zscore,
    estimate_cointegration_half_life,
    calculate_relative_strength,
)
from sector_quant.strategies.pairs_trading import (
    SectorPairsTradingStrategy,
    PairConfig,
    PairPositionState,
)
from sector_quant.strategies.sector_momentum import (
    SectorMomentumStrategy,
    SectorUniverseConfig,
)

__all__ = [
    "Strategy",
    "rolling_ols",
    "calculate_spread_and_zscore",
    "estimate_cointegration_half_life",
    "calculate_relative_strength",
    "SectorPairsTradingStrategy",
    "PairConfig",
    "PairPositionState",
    "SectorMomentumStrategy",
    "SectorUniverseConfig",
]

