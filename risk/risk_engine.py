"""Risk Engine with Pre-Trade RMS and Circuit Breakers."""

from __future__ import annotations

import logging
from typing import List, Tuple
from core.interfaces import BaseRiskEngine
from core.models import OrderRequest, AccountBalance, Position
from risk.market_clock import MarketClock
from risk.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class RiskEngine(BaseRiskEngine):
    """Pre-trade Risk Management System (RMS) ensuring regulatory & risk compliance."""

    def __init__(
        self,
        max_daily_loss: float = 3000.0,
        max_open_positions: int = 3,
        rate_limiter: RateLimiter = None,
        market_clock: MarketClock = None,
    ):
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions
        self.rate_limiter = rate_limiter or RateLimiter(rate=5.0)
        self.market_clock = market_clock or MarketClock()
        self.circuit_breaker_triggered = False

    def validate_order(
        self,
        request: OrderRequest,
        current_balance: AccountBalance,
        current_positions: List[Position],
    ) -> Tuple[bool, str]:
        """Verify order against market session, daily loss circuit breaker, margins, and limits."""
        # 1. Rate limiter check
        if not self.rate_limiter.acquire(blocking=False):
            return False, "RMS Rejected: Rate limit exceeded (>5 orders/sec)."

        # 2. Daily Loss Circuit Breaker
        total_pnl = current_balance.realized_pnl + current_balance.unrealized_pnl
        if total_pnl <= -abs(self.max_daily_loss):
            self.circuit_breaker_triggered = True
            logger.critical(f"RMS CIRCUIT BREAKER ACTIVATED: Loss ₹{abs(total_pnl):.2f} exceeded max daily limit ₹{self.max_daily_loss:.2f}")
            return False, f"RMS Rejected: Daily max loss limit (-₹{self.max_daily_loss}) hit. Trading halted for the day."

        # 3. Market Clock Check (for entering new positions)
        if not self.market_clock.is_normal_trading_active():
            # Allow order if it is closing an existing position
            has_open_pos = any(p.symbol == request.symbol and p.quantity != 0 for p in current_positions)
            if not has_open_pos:
                return False, "RMS Rejected: Outside normal trading window (09:15 - 15:15 IST)."

        # 4. Max Open Positions Check
        active_positions = [p for p in current_positions if p.quantity != 0]
        is_new_symbol = not any(p.symbol == request.symbol for p in active_positions)
        if is_new_symbol and len(active_positions) >= self.max_open_positions:
            return False, f"RMS Rejected: Max open positions limit ({self.max_open_positions}) reached."

        # 5. Margin Check
        estimated_margin_required = (request.price or 100.0) * request.quantity * 0.20
        if estimated_margin_required > current_balance.available_margin:
            return False, f"RMS Rejected: Insufficient margin. Required ₹{estimated_margin_required:.2f}, Available ₹{current_balance.available_margin:.2f}"

        return True, "RMS Approved"
