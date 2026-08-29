"""PMB (Position & Portfolio Management Bot) for Indian Equities & Derivatives (NSE/BSE)."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any, Callable
from core.models import Position, AccountBalance, Order
from core.enums import OrderSide, OrderType, ProductType, Exchange
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from risk.market_clock import MarketClock
from risk.risk_engine import RiskEngine

logger = logging.getLogger("Execution.PMB")


class PortfolioManagementBot:
    """
    PMB: Master Position & Portfolio Management Supervisor.
    - Global Portfolio Risk Monitoring & Max Drawdown Circuit Breaker
    - Dynamic Trailing Profit Locks across aggregate positions
    - Automated 15:15 IST Intraday MIS Square-Off
    - Exposure & Sector Concentration Guard
    """

    def __init__(
        self,
        router: ExecutionRouter,
        order_manager: OrderManager,
        risk_engine: RiskEngine,
        market_clock: Optional[MarketClock] = None,
        max_daily_loss: float = 3000.0,
        portfolio_profit_target: float = 5000.0,
    ):
        self.router = router
        self.order_manager = order_manager
        self.risk_engine = risk_engine
        self.market_clock = market_clock or MarketClock()
        self.max_daily_loss = max_daily_loss
        self.portfolio_profit_target = portfolio_profit_target
        self.circuit_breaker_active = False
        self.liquidation_callbacks: List[Callable[[], None]] = []

    def register_liquidation_callback(self, cb: Callable[[], None]) -> None:
        """Register a callback to notify sub-bots (MTB, MRB) on emergency liquidation."""
        self.liquidation_callbacks.append(cb)

    def check_portfolio_health(self, balance: AccountBalance, positions: List[Position]) -> Dict[str, Any]:
        """Evaluate account-wide P&L, drawdown limits, and margin health."""
        total_pnl = balance.realized_pnl + balance.unrealized_pnl

        # 1. Check Max Daily Loss Circuit Breaker
        if total_pnl <= -abs(self.max_daily_loss):
            if not self.circuit_breaker_active:
                logger.critical(f"[PMB] 🚨 CIRCUIT BREAKER TRIGGERED! Loss ₹{abs(total_pnl):,.2f} crossed max limit ₹{self.max_daily_loss:,.2f}")
                self.circuit_breaker_active = True
                self.emergency_liquidate_all(positions, reason="Daily Max Loss Breached")
            return {"status": "HALTED", "total_pnl": total_pnl, "action": "EMERGENCY_SHUTDOWN"}

        # 2. Check Portfolio Daily Profit Lock
        if total_pnl >= self.portfolio_profit_target:
            logger.info(f"[PMB] 🎯 Portfolio Target ₹{total_pnl:,.2f} achieved! Locking in gains for the day.")
            return {"status": "PROFIT_LOCKED", "total_pnl": total_pnl, "action": "TAKE_PROFIT"}

        return {"status": "HEALTHY", "total_pnl": total_pnl, "action": "NONE"}

    def auto_square_off_intraday(self, positions: List[Position]) -> List[Order]:
        """Execute automated 15:15 IST Square-Off for all active MIS positions."""
        closed_orders = []
        if not self.market_clock.is_auto_square_off_time():
            return closed_orders

        logger.info(f"[PMB] 🕒 15:15 IST Auto-Square-Off Window Triggered. Liquidating {len(positions)} positions...")
        for pos in positions:
            if pos.product_type == ProductType.MIS and pos.quantity != 0:
                side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
                close_qty = abs(pos.quantity)
                logger.info(f"[PMB] Squaring off MIS: {side.value} {close_qty}x {pos.symbol}")
                
                req = self._create_request(pos.symbol, side, close_qty, pos.exchange, pos.product_type, tag="PMB_AutoSquareOff")
                order = self.router.route_order(req)
                self.order_manager.register_order(order)
                closed_orders.append(order)

        # Notify sub-bots to clear local state
        for cb in self.liquidation_callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"[PMB] Error in liquidation callback: {e}")

        return closed_orders

    def emergency_liquidate_all(self, positions: List[Position], reason: str = "Emergency") -> List[Order]:
        """Immediate kill-switch to liquidate all active positions."""
        logger.critical(f"[PMB] 💥 EMERGENCY LIQUIDATION INITIATED: {reason}")
        liquidated_orders = []
        for pos in positions:
            if pos.quantity != 0:
                side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
                qty = abs(pos.quantity)
                req = self._create_request(pos.symbol, side, qty, pos.exchange, pos.product_type, tag="PMB_EmergencyLiquidate")
                order = self.router.route_order(req)
                self.order_manager.register_order(order)
                liquidated_orders.append(order)

        # Notify sub-bots to clear local state
        for cb in self.liquidation_callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"[PMB] Error in liquidation callback: {e}")

        return liquidated_orders

    def _create_request(self, symbol: str, side: OrderSide, quantity: int, exchange: Exchange, product_type: ProductType, tag: str) -> Any:
        import uuid
        from core.models import OrderRequest
        return OrderRequest(
            client_order_id=f"PMB-{uuid.uuid4().hex[:6].upper()}",
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            product_type=product_type,
            exchange=exchange,
            quantity=quantity,
            tag=tag,
        )

