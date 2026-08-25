"""Position Sizer based on Risk per Trade, Capital, and Lot Size Quantization."""

from __future__ import annotations

import math
from typing import Optional


class PositionSizer:
    """Calculates optimal share/contract quantity according to risk rules."""

    @staticmethod
    def calculate_quantity(
        capital: float,
        risk_per_trade_pct: float,
        entry_price: float,
        stop_loss_price: float,
        lot_size: int = 1,
        margin_pct: float = 20.0,
    ) -> int:
        """
        Calculate quantity:
        - Risk Amount = Capital * (Risk % / 100)
        - Risk per share = abs(Entry - SL)
        - Raw Qty = Risk Amount / Risk per share
        - Clamped by margin availability
        - Quantized to multiple of lot_size
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            return 0

        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share <= 0:
            return 0

        max_risk_amount = capital * (risk_per_trade_pct / 100.0)
        raw_qty = max_risk_amount / risk_per_share

        # Margin required per share/contract (e.g. 20% for MIS / Futures)
        margin_per_unit = entry_price * (margin_pct / 100.0)
        max_qty_by_margin = capital / margin_per_unit if margin_per_unit > 0 else raw_qty
        final_qty = min(raw_qty, max_qty_by_margin)

        # Lot size quantization
        if lot_size > 1:
            lots = round(final_qty / lot_size)
            return max(lot_size if lots == 0 and final_qty >= (lot_size * 0.5) else 0, lots * lot_size)

        return max(1, math.floor(final_qty))
