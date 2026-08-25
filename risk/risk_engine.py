"""
Indian Market Risk Management System (RMS) Engine.
Enforces daily max loss, session guards, order frequency limits, and pre-trade validations.
"""

from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Dict, List, Optional
from config.config_loader import BotSettings
from core.enums import MarketSession, OrderSide, ProductType
from core.interfaces import BaseRiskEngine
from core.models import AccountBalance, OrderRequest, RiskCheckResult, Trade
from risk.market_clock import MarketClock
from risk.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class RiskEngine(BaseRiskEngine):
    """
    Core pre-trade and post-trade risk management engine.
    """

    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self.market_clock = MarketClock(
            pre_market_start=settings.market_clock.pre_market_start,
            market_open=settings.market_clock.market_open,
            auto_square_off=settings.market_clock.auto_square_off,
            market_close=settings.market_clock.market_close,
        )
        self.rate_limiter = RateLimiter(
            rate=settings.risk.max_orders_per_second,
            burst=settings.risk.rate_limit_burst,
        )

        self.max_daily_loss = settings.risk.max_daily_loss_inr
        self.max_daily_trades = settings.risk.max_daily_trades
        self.max_open_positions = settings.risk.max_open_positions

        # Internal tracking
        self.daily_pnl = 0.0
        self.daily_trades_count = 0
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason: Optional[str] = None
        self.current_date = date.today()

    def reset_daily_metrics_if_new_day(self) -> None:
        """Reset counters on a new trading day."""
        today = date.today()
        if today != self.current_date:
            logger.info(f"Resetting RMS daily metrics for new trading date: {today}")
            self.daily_pnl = 0.0
            self.daily_trades_count = 0
            self.circuit_breaker_tripped = False
            self.circuit_breaker_reason = None
            self.current_date = today

    def evaluate_order(self, request: OrderRequest, account_balance: AccountBalance) -> RiskCheckResult:
        """
        Comprehensive pre-trade RMS validation.
        """
        self.reset_daily_metrics_if_new_day()

        # 1. Circuit Breaker Check
        if self.circuit_breaker_tripped:
            return RiskCheckResult(
                allowed=False,
                reason=f"RMS Circuit Breaker is ACTIVE: {self.circuit_breaker_reason}",
            )

        # 2. Market Session Guard (IST)
        session = self.market_clock.get_session()
        if session != MarketSession.TRADING:
            return RiskCheckResult(
                allowed=False,
                reason=f"Order rejected: Market is currently in '{session.value}' session. Trading allowed only 09:15-15:15 IST.",
            )

        # 3. Rate Limiter (Throttling)
        if not self.rate_limiter.acquire(tokens=1, blocking=False):
            return RiskCheckResult(
                allowed=False,
                reason="Order rate limit exceeded (throttled to protect against broker API bans).",
            )

        # 4. Max Daily Trades Check
        if self.daily_trades_count >= self.max_daily_trades:
            self.trip_circuit_breaker(f"Reached max daily trades limit ({self.max_daily_trades}).")
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily trade limit reached ({self.max_daily_trades}).",
            )

        # 5. Margin Sufficiency Check
        est_price = request.price or 100.0
        req_margin = est_price * request.quantity
        if request.product == ProductType.MIS:
            req_margin *= 0.20  # Intraday 5x leverage

        if request.side == OrderSide.BUY and req_margin > account_balance.available_margin:
            return RiskCheckResult(
                allowed=False,
                reason=f"Insufficient margin. Required ₹{req_margin:,.2f} > Available ₹{account_balance.available_margin:,.2f}",
            )

        return RiskCheckResult(allowed=True)

    def record_trade(self, trade: Trade) -> None:
        """
        Update daily metrics after a trade fill and check loss thresholds.
        """
        self.reset_daily_metrics_if_new_day()
        self.daily_trades_count += 1

        # Check daily loss limit against accumulated P&L
        if self.daily_pnl <= -self.max_daily_loss:
            self.trip_circuit_breaker(
                f"Daily loss limit breached: Current Daily P&L = -₹{abs(self.daily_pnl):,.2f} "
                f"(Threshold: -₹{self.max_daily_loss:,.2f})"
            )

    def update_daily_pnl(self, realized_pnl: float, unrealized_pnl: float) -> None:
        """Update total daily P&L and evaluate circuit breaker."""
        self.reset_daily_metrics_if_new_day()
        total_pnl = realized_pnl + unrealized_pnl
        self.daily_pnl = total_pnl

        if total_pnl <= -self.max_daily_loss and not self.circuit_breaker_tripped:
            self.trip_circuit_breaker(
                f"Max daily loss breached! Total P&L: ₹{total_pnl:,.2f} <= -₹{self.max_daily_loss:,.2f}"
            )

    def trip_circuit_breaker(self, reason: str) -> None:
        """Trip circuit breaker and halt further trade placement."""
        self.circuit_breaker_tripped = True
        self.circuit_breaker_reason = reason
        logger.critical(f"🚨 RMS CIRCUIT BREAKER TRIPPED: {reason}")

    def is_circuit_breaker_active(self) -> bool:
        return self.circuit_breaker_tripped
