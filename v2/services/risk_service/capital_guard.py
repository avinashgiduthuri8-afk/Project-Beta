"""RMS Capital Guard and Single-Asset Invariant Enforcement."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Set, Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass
class CapitalGuardConfig:
    single_asset_lock: bool = True       # Max 1 active position per symbol across fleet
    max_total_capital: float = 1000000.0 # Max total portfolio deployed capital
    max_single_stock_pct: float = 0.15   # Max 15% of portfolio in any single stock
    max_daily_loss_pct: float = 0.03     # 3% Max Daily Drawdown circuit breaker
    min_cash_buffer_pct: float = 0.05    # 5% Cash Buffer


class RMSCapitalGuard:
    """Enforces mathematical safety invariants for stock algorithmic trading."""

    def __init__(self, config: Optional[CapitalGuardConfig] = None):
        self.config = config or CapitalGuardConfig()
        self.circuit_breaker_tripped = False
        self.daily_starting_equity = 1000000.0

    def set_starting_equity(self, equity: float) -> None:
        self.daily_starting_equity = equity

    def validate_new_trade(
        self,
        symbol: str,
        notional_cost: float,
        current_equity: float,
        current_cash: float,
        active_positions: Dict[str, Any],
        realized_daily_pnl: float = 0.0,
        unrealized_daily_pnl: float = 0.0,
    ) -> Tuple[bool, str]:
        """Validates trade setup against Single-Asset Lock, Capital Caps, and Circuit Breakers.

        Returns (is_approved: bool, rejection_reason: str)
        """
        clean_sym = symbol.upper()

        # 1. Check Circuit Breaker
        total_daily_pnl = realized_daily_pnl + unrealized_daily_pnl
        if self.daily_starting_equity > 0:
            drawdown_pct = abs(min(0.0, total_daily_pnl)) / self.daily_starting_equity
            if drawdown_pct >= self.config.max_daily_loss_pct:
                self.circuit_breaker_tripped = True
                return False, f"Daily drawdown circuit breaker tripped ({drawdown_pct*100:.2f}% >= {self.config.max_daily_loss_pct*100:.1f}%)"

        if self.circuit_breaker_tripped:
            return False, "Circuit breaker is active. New entry orders blocked."

        # 2. Single-Asset Lock Invariant
        if self.config.single_asset_lock:
            # Check if symbol already has an active open position in fleet
            if clean_sym in active_positions:
                return False, f"Single-Asset Lock Violation: Active position already exists for {clean_sym}"

        # 3. Single-Stock Allocation Cap
        max_allowed_stock_notional = current_equity * self.config.max_single_stock_pct
        if notional_cost > max_allowed_stock_notional:
            return False, f"Single-Stock Cap Exceeded for {clean_sym} ({notional_cost:.2f} > Max {max_allowed_stock_notional:.2f})"

        # 4. Total Capital Deployment Cap
        current_deployed = sum(float(p.get("notional", p.get("qty", 0) * p.get("entry_price", 0))) for p in active_positions.values())
        if (current_deployed + notional_cost) > self.config.max_total_capital:
            return False, f"Max Total Portfolio Capital Limit Exceeded ({current_deployed + notional_cost:.2f} > {self.config.max_total_capital:.2f})"

        # 5. Cash Buffer Retention
        required_cash_buffer = current_equity * self.config.min_cash_buffer_pct
        if (current_cash - notional_cost) < required_cash_buffer:
            return False, f"Insufficient Cash Buffer (Remaining Cash: {current_cash - notional_cost:.2f} < Required: {required_cash_buffer:.2f})"

        return True, "RMS Approved"

