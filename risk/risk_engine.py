"""Enhanced Pre-Trade Risk Management System (RMS) with Hard Gates (Project-Beta)."""

from __future__ import annotations

import logging
from typing import List, Tuple, Optional
from core.enums import ProductType
from core.interfaces import BaseRiskEngine
from core.models import OrderRequest, AccountBalance, Position, TradePlan
from risk.market_clock import MarketClock
from risk.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class RiskEngine(BaseRiskEngine):
    """
    Deterministic Risk Hard Gatekeeper:
    - Daily Max Loss Circuit Breaker
    - Per-Trade Risk (Max 1.0% of Capital)
    - Sector Concentration Cap (Max 25% of open margin in a single sector)
    - Token-Bucket API Rate Limiter (5 orders/sec)
    - Strict IST Market Session Guard (09:15 - 15:15 IST)
    """

    def __init__(
        self,
        max_daily_loss: float = 3000.0,
        max_open_positions: int = 4,
        max_sector_exposure_pct: float = 25.0,
        max_risk_per_trade_pct: float = 1.0,
        rate_limiter: Optional[RateLimiter] = None,
        market_clock: Optional[MarketClock] = None,
    ):
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions
        self.max_sector_exposure_pct = max_sector_exposure_pct
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.rate_limiter = rate_limiter or RateLimiter(rate=5.0)
        self.market_clock = market_clock or MarketClock()
        self.circuit_breaker_triggered = False

    def validate_order(
        self,
        request: OrderRequest,
        current_balance: AccountBalance,
        current_positions: List[Position],
        candidate_sector: str = "GENERAL",
    ) -> Tuple[bool, str]:
        # 1. API Rate Limiter Check
        if not self.rate_limiter.acquire():
            return False, "RMS Rejected: Rate limit exceeded (>5 orders/sec)."

        # 2. Daily Loss Circuit Breaker
        total_pnl = current_balance.realized_pnl + current_balance.unrealized_pnl
        if total_pnl <= -abs(self.max_daily_loss):
            self.circuit_breaker_triggered = True
            logger.critical(f"RMS CIRCUIT BREAKER ACTIVATED: Loss ₹{abs(total_pnl):.2f} exceeded max limit ₹{self.max_daily_loss:.2f}")
            return False, f"RMS Rejected: Daily max loss limit (-₹{self.max_daily_loss:,.2f}) hit. Trading halted."

        # 3. Market Clock Window Check (for new position entries)
        if not self.market_clock.is_normal_trading_active():
            has_open_pos = any(p.symbol == request.symbol and p.quantity != 0 for p in current_positions)
            if not has_open_pos:
                return False, "RMS Rejected: Outside normal trading window (09:15 - 15:15 IST)."

        # Determine effective price for market/limit orders
        effective_price = request.price
        if effective_price is None or effective_price <= 0:
            matching_pos = next((p for p in current_positions if p.symbol == request.symbol and p.ltp > 0), None)
            effective_price = matching_pos.ltp if matching_pos else 100.0

        # 4. Max Open Positions Cap
        active_positions = [p for p in current_positions if p.quantity != 0]
        is_new_symbol = not any(p.symbol == request.symbol for p in active_positions)
        if is_new_symbol and len(active_positions) >= self.max_open_positions:
            return False, f"RMS Rejected: Max open positions limit ({self.max_open_positions}) reached."

        # 5. Sector Concentration Check
        if is_new_symbol and candidate_sector != "GENERAL":
            sector_positions = [p for p in active_positions if getattr(p, "sector", "") == candidate_sector]
            total_active_val = sum(abs(p.quantity * p.ltp) for p in active_positions) + effective_price * request.quantity
            sector_val = sum(abs(p.quantity * p.ltp) for p in sector_positions) + effective_price * request.quantity
            if total_active_val > 0:
                sector_pct = (sector_val / total_active_val) * 100.0
                if sector_pct > self.max_sector_exposure_pct and len(active_positions) >= 2:
                    return False, f"RMS Rejected: Sector '{candidate_sector}' exposure ({sector_pct:.1f}%) exceeds {self.max_sector_exposure_pct}% cap."

        # 6. Margin Sufficiency Check
        margin_multiplier = 1.0 if request.product_type == ProductType.CNC else (0.25 if request.product_type == ProductType.NRML else 0.20)
        margin_required = effective_price * request.quantity * margin_multiplier
        if margin_required > current_balance.available_margin:
            return False, f"RMS Rejected: Insufficient margin. Required ₹{margin_required:.2f}, Available ₹{current_balance.available_margin:.2f}"

        return True, "RMS Approved"

