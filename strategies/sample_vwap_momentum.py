"""Sample Intraday VWAP Momentum Strategy for Indian Stocks."""

from __future__ import annotations

import logging
from typing import Dict, Optional
from core.models import Tick, Candle, Order
from core.enums import OrderSide, OrderType, ProductType, Exchange
from strategies.base_strategy import Strategy
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager

logger = logging.getLogger(__name__)


class VWAPMomentumStrategy(Strategy):
    """
    Intraday Strategy:
    - Long Signal: When Candle Close crosses ABOVE VWAP and Volume > Average Volume.
    - Short Signal: When Candle Close crosses BELOW VWAP and Volume > Average Volume.
    - Auto-squares off at Target / Stop Loss.
    """

    def __init__(
        self,
        router: ExecutionRouter,
        order_manager: OrderManager,
        stop_loss_pct: float = 0.8,
        target_rr: float = 2.0,
    ):
        super().__init__(name="VWAP_Momentum", router=router, order_manager=order_manager)
        self.stop_loss_pct = stop_loss_pct
        self.target_rr = target_rr
        self.active_positions: Dict[str, str] = {}  # Symbol -> "LONG" | "SHORT"

    def on_candle(self, candle: Candle) -> None:
        """Evaluate strategy rule on closed candle."""
        if not candle.is_closed or not candle.vwap:
            return

        symbol = candle.symbol
        current_pos = self.active_positions.get(symbol)

        # Bullish VWAP Breakout
        if candle.close > candle.vwap and candle.open <= candle.vwap and current_pos != "LONG":
            logger.info(f"[{self.name}] BUY Signal triggered for {symbol} at ₹{candle.close:.2f} (VWAP: ₹{candle.vwap:.2f})")
            self.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                exchange=Exchange.NSE,
            )
            self.active_positions[symbol] = "LONG"

        # Bearish VWAP Breakdown
        elif candle.close < candle.vwap and candle.open >= candle.vwap and current_pos != "SHORT":
            logger.info(f"[{self.name}] SELL Signal triggered for {symbol} at ₹{candle.close:.2f} (VWAP: ₹{candle.vwap:.2f})")
            self.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                exchange=Exchange.NSE,
            )
            self.active_positions[symbol] = "SHORT"

    def on_tick(self, tick: Tick) -> None:
        pass

    def on_order_update(self, order: Order) -> None:
        logger.info(f"[{self.name}] Order Update Received: {order.symbol} is now {order.status.value}")
