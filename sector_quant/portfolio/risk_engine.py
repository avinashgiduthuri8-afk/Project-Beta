"""Sector Exposure & Portfolio Risk Management Engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_sector_allocation: float = 0.30  # Max 30% of total equity in any single sector
    max_stock_allocation: float = 0.15   # Max 15% of total equity in any single stock
    max_gross_leverage: float = 1.5      # Max 150% gross exposure
    min_cash_buffer_pct: float = 0.05    # Keep at least 5% in cash buffer


class SectorRiskEngine:
    """Enforces pre-trade sector concentration limits, single-stock caps, and leverage constraints."""

    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()

    def validate_order(
        self,
        symbol: str,
        sector: str,
        order_direction: str,
        quantity: int,
        price: float,
        current_equity: float,
        current_cash: float,
        current_positions: Dict[str, int],
        current_prices: Dict[str, float],
        symbol_to_sector: Dict[str, str],
    ) -> Tuple[bool, str, int]:
        """Validates if an order complies with portfolio risk rules.

        Returns
        -------
        tuple of (is_approved: bool, rejection_reason: str, adjusted_quantity: int)
        """
        if current_equity <= 0 or price <= 0 or quantity <= 0:
            return False, "Invalid equity or price or quantity", 0

        order_cost = quantity * price

        # If reducing/closing an existing position (e.g. sell when long or buy when short), allow it
        existing_qty = current_positions.get(symbol.upper(), 0)
        if (existing_qty > 0 and order_direction.upper() == "SELL") or (existing_qty < 0 and order_direction.upper() == "BUY"):
            # Exiting / de-risking trade
            return True, "Risk Approved (Position Reduction)", quantity

        # 1. Cash buffer check
        required_cash = order_cost + (current_equity * self.limits.min_cash_buffer_pct)
        if current_cash < required_cash and order_direction.upper() == "BUY":
            available_for_trade = max(0.0, current_cash - (current_equity * self.limits.min_cash_buffer_pct))
            adjusted_qty = int(available_for_trade // price)
            if adjusted_qty <= 0:
                return False, f"Insufficient cash buffer (Need {required_cash:.2f}, Have {current_cash:.2f})", 0
            quantity = adjusted_qty
            order_cost = quantity * price

        # 2. Single-stock allocation cap check
        projected_stock_exposure = abs(existing_qty * price) + order_cost
        max_allowed_stock_exposure = current_equity * self.limits.max_stock_allocation
        if projected_stock_exposure > max_allowed_stock_exposure:
            remaining_cap = max(0.0, max_allowed_stock_exposure - abs(existing_qty * price))
            adjusted_qty = int(remaining_cap // price)
            if adjusted_qty <= 0:
                return False, f"Single-stock cap exceeded for {symbol} ({self.limits.max_stock_allocation*100:.0f}%)", 0
            quantity = adjusted_qty
            order_cost = quantity * price

        # 3. Sector concentration limit check
        sec_code = (sector or symbol_to_sector.get(symbol.upper(), "GENERAL")).upper()
        current_sector_exposure = 0.0
        for sym, qty in current_positions.items():
            sym_sec = symbol_to_sector.get(sym, "GENERAL").upper()
            if sym_sec == sec_code:
                current_sector_exposure += abs(qty * current_prices.get(sym, price))

        projected_sector_exposure = current_sector_exposure + order_cost
        max_allowed_sector_exposure = current_equity * self.limits.max_sector_allocation
        if projected_sector_exposure > max_allowed_sector_exposure:
            remaining_sec_cap = max(0.0, max_allowed_sector_exposure - current_sector_exposure)
            adjusted_qty = int(remaining_sec_cap // price)
            if adjusted_qty <= 0:
                return False, f"Sector limit exceeded for {sec_code} ({self.limits.max_sector_allocation*100:.0f}%)", 0
            quantity = adjusted_qty
            order_cost = quantity * price

        # 4. Gross leverage check
        total_gross_exposure = sum(abs(q * current_prices.get(s, price)) for s, q in current_positions.items()) + order_cost
        if total_gross_exposure > current_equity * self.limits.max_gross_leverage:
            return False, f"Gross leverage exceeded ({self.limits.max_gross_leverage:.1f}x)", 0

        return True, "Risk Approved", quantity

