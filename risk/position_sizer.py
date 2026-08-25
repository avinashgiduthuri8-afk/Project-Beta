"""
Position Sizer for Indian Equities & Derivatives.
Calculates position size based on capital, risk per trade, stop-loss distance in ₹, and lot size.
"""

from __future__ import annotations

import math
from typing import Optional
from core.enums import ProductType


class PositionSizer:
    """
    Calculates appropriate order quantity respecting risk parameters and lot sizes.
    """

    @staticmethod
    def calculate_quantity_by_risk(
        capital: float,
        risk_pct: float,
        entry_price: float,
        stop_loss_price: float,
        lot_size: int = 1,
        product: ProductType = ProductType.MIS,
        max_capital_allocation_pct: float = 20.0,
    ) -> int:
        """
        Calculate quantity based on fixed risk per trade.

        Risk Amount = Capital * (risk_pct / 100)
        Stop Loss Distance (₹) = abs(entry_price - stop_loss_price)
        Raw Quantity = Risk Amount / Stop Loss Distance

        Args:
            capital: Available trading capital (₹).
            risk_pct: Percentage of capital to risk (e.g. 1.0 = 1%).
            entry_price: Planned entry price (₹).
            stop_loss_price: Planned stop loss price (₹).
            lot_size: Instrument lot size (1 for stocks, 50 for NIFTY, etc.).
            product: MIS (intraday) or CNC / NRML.
            max_capital_allocation_pct: Max percentage of capital for this single trade.

        Returns:
            Calculated and lot-adjusted quantity (minimum 0 or 1 lot).
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            return 0

        sl_distance = abs(entry_price - stop_loss_price)
        if sl_distance <= 0.01:
            return 0

        risk_amount = capital * (risk_pct / 100.0)
        raw_quantity = risk_amount / sl_distance

        # Capital cap check
        max_allocation = capital * (max_capital_allocation_pct / 100.0)
        leverage = 5.0 if product == ProductType.MIS else 1.0
        max_qty_by_capital = (max_allocation * leverage) / entry_price

        final_qty = min(raw_quantity, max_qty_by_capital)

        # Quantize to lot size
        if lot_size > 1:
            lots = max(1, math.floor(final_qty / lot_size))
            return int(lots * lot_size)
        else:
            return max(1, math.floor(final_qty))
