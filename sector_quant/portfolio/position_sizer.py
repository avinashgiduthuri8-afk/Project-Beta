"""Position Sizing Module for quantitative sector allocation and pairs trading."""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculates order quantities based on target allocation, volatility, and pair hedge ratios."""

    def __init__(
        self,
        default_stock_weight: float = 0.10,
        vol_target_annual: float = 0.15,
        min_lot_size: int = 1,
    ):
        self.default_stock_weight = default_stock_weight
        self.vol_target_annual = vol_target_annual
        self.min_lot_size = min_lot_size

    def calculate_fixed_fraction_quantity(
        self,
        equity: float,
        price: float,
        weight: Optional[float] = None,
    ) -> int:
        """Computes integer share quantity for fixed portfolio capital allocation percentage."""
        if equity <= 0 or price <= 0:
            return 0

        target_weight = weight if weight is not None else self.default_stock_weight
        target_capital = equity * target_weight
        raw_qty = target_capital / price
        qty = int(raw_qty // self.min_lot_size) * self.min_lot_size
        return max(0, qty)

    def calculate_pairs_quantities(
        self,
        equity: float,
        y_price: float,
        x_price: float,
        beta: float,
        total_pair_weight: float = 0.20,
    ) -> tuple[int, int]:
        """Computes balanced integer share lots (Q_Y, Q_X) for an intra-sector pair.

        Parameters
        ----------
        equity : float
            Total current portfolio equity.
        y_price : float
            Current price of stock Y.
        x_price : float
            Current price of stock X.
        beta : float
            OLS hedge ratio (Y = alpha + beta * X).
        total_pair_weight : float
            Total capital allocated to both legs of the pair.

        Returns
        -------
        tuple of (q_y: int, q_x: int)
        """
        if equity <= 0 or y_price <= 0 or x_price <= 0 or beta <= 0:
            return 0, 0

        allocated_capital = equity * total_pair_weight

        # Spread portfolio: 1 share of Y and beta shares of X
        unit_cost = y_price + (abs(beta) * x_price)
        if unit_cost <= 0:
            return 0, 0

        num_spread_units = allocated_capital / unit_cost
        q_y = int(max(1, round(num_spread_units)))
        q_x = int(max(1, round(beta * q_y)))

        return q_y, q_x

    def calculate_volatility_adjusted_quantity(
        self,
        equity: float,
        price: float,
        daily_volatility: float,
        target_risk_dollars: Optional[float] = None,
    ) -> int:
        """Inverse-volatility sizing targeting a constant volatility risk contribution."""
        if equity <= 0 or price <= 0 or daily_volatility <= 0:
            return 0

        target_risk = target_risk_dollars if target_risk_dollars is not None else (equity * (self.vol_target_annual / (252 ** 0.5)))
        dollar_vol_per_share = price * daily_volatility

        if dollar_vol_per_share <= 0:
            return 0

        raw_qty = target_risk / dollar_vol_per_share
        qty = int(raw_qty // self.min_lot_size) * self.min_lot_size
        return max(0, qty)

