"""MTB (Momentum Trading Bot) for Indian Equities & Derivatives (NSE/BSE)."""

from __future__ import annotations

import logging
from typing import Dict, Optional
from core.models import Tick, Candle, Order, Position
from core.enums import OrderSide, OrderType, ProductType, Exchange
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from risk.position_sizer import PositionSizer
from risk.market_clock import MarketClock

logger = logging.getLogger("Execution.MTB")


class MomentumTradingBot:
    """
    MTB: Executes momentum, breakout, and trend-following trades.
    - Opening Range Breakout (ORB)
    - VWAP + Volume Breakout
    - Trailing Stop-Loss Management with ₹0.05 tick rounding
    """

    def __init__(
        self,
        router: ExecutionRouter,
        order_manager: OrderManager,
        market_clock: Optional[MarketClock] = None,
        risk_per_trade_pct: float = 1.0,
        trailing_sl_pct: float = 0.5,
        target_rr: float = 2.0,
    ):
        self.router = router
        self.order_manager = order_manager
        self.market_clock = market_clock or MarketClock()
        self.risk_per_trade_pct = risk_per_trade_pct
        self.trailing_sl_pct = trailing_sl_pct
        self.target_rr = target_rr

        # Tracking state: symbol -> {"entry": float, "sl": float, "target": float, "side": OrderSide, "qty": int}
        self.active_trades: Dict[str, Dict] = {}

    def on_candle(self, candle: Candle, capital: float = 100000.0, lot_size: int = 1) -> Optional[Order]:
        """Process completed candle and trigger momentum breakout entries."""
        if not candle.is_closed or not candle.vwap:
            return None

        symbol = candle.symbol
        if symbol in self.active_trades:
            return None  # Already in an active MTB trade

        # 1. Bullish Momentum Breakout: Close > VWAP and Close > Open (Green Candle)
        if candle.close > candle.vwap and candle.open <= candle.vwap and candle.close > candle.open:
            entry_price = self.router.normalize_tick_size(candle.close)
            sl_price = self.router.normalize_tick_size(entry_price * (1.0 - (self.trailing_sl_pct / 100.0)))
            target_price = self.router.normalize_tick_size(entry_price + (entry_price - sl_price) * self.target_rr)

            quantity = PositionSizer.calculate_quantity(
                capital=capital,
                risk_per_trade_pct=self.risk_per_trade_pct,
                entry_price=entry_price,
                stop_loss_price=sl_price,
                lot_size=lot_size,
            )

            if quantity <= 0:
                return None

            logger.info(f"[MTB] 🚀 Bullish Momentum Signal on {symbol}: Entry=₹{entry_price:.2f}, SL=₹{sl_price:.2f}, Qty={quantity}")
            
            # Route Entry Order
            req = self._create_request(symbol, OrderSide.BUY, quantity, entry_price)
            order = self.router.route_order(req, lot_size=lot_size)
            self.order_manager.register_order(order)

            self.active_trades[symbol] = {
                "entry": entry_price,
                "sl": sl_price,
                "target": target_price,
                "side": OrderSide.BUY,
                "qty": quantity,
                "lot_size": lot_size,
            }
            return order

        # 2. Bearish Momentum Breakdown: Close < VWAP and Close < Open (Red Candle)
        elif candle.close < candle.vwap and candle.open >= candle.vwap and candle.close < candle.open:
            entry_price = self.router.normalize_tick_size(candle.close)
            sl_price = self.router.normalize_tick_size(entry_price * (1.0 + (self.trailing_sl_pct / 100.0)))
            target_price = self.router.normalize_tick_size(entry_price - (sl_price - entry_price) * self.target_rr)

            quantity = PositionSizer.calculate_quantity(
                capital=capital,
                risk_per_trade_pct=self.risk_per_trade_pct,
                entry_price=entry_price,
                stop_loss_price=sl_price,
                lot_size=lot_size,
            )

            if quantity <= 0:
                return None

            logger.info(f"[MTB] 🔻 Bearish Momentum Signal on {symbol}: Entry=₹{entry_price:.2f}, SL=₹{sl_price:.2f}, Qty={quantity}")

            # Route Short Order
            req = self._create_request(symbol, OrderSide.SELL, quantity, entry_price)
            order = self.router.route_order(req, lot_size=lot_size)
            self.order_manager.register_order(order)

            self.active_trades[symbol] = {
                "entry": entry_price,
                "sl": sl_price,
                "target": target_price,
                "side": OrderSide.SELL,
                "qty": quantity,
                "lot_size": lot_size,
            }
            return order

        return None

    def on_tick(self, tick: Tick) -> Optional[Order]:
        """Manage active trailing stop-loss and profit target executions on live ticks."""
        symbol = tick.symbol
        if symbol not in self.active_trades:
            return None

        trade = self.active_trades[symbol]
        ltp = tick.ltp

        # Trailing Stop & Target for LONG
        if trade["side"] == OrderSide.BUY:
            # Trailing SL update if price made a new high
            if ltp > trade["entry"]:
                new_sl = self.router.normalize_tick_size(ltp * (1.0 - (self.trailing_sl_pct / 100.0)))
                if new_sl > trade["sl"]:
                    trade["sl"] = new_sl

            # Target or SL Hit
            if ltp >= trade["target"] or ltp <= trade["sl"]:
                exit_reason = "Target Hit 🎯" if ltp >= trade["target"] else "Stop-Loss Hit 🛑"
                logger.info(f"[MTB] {exit_reason} for {symbol} @ ₹{ltp:.2f} (SL: ₹{trade['sl']:.2f}, Target: ₹{trade['target']:.2f})")
                req = self._create_request(symbol, OrderSide.SELL, trade["qty"], ltp)
                order = self.router.route_order(req, lot_size=trade["lot_size"])
                self.order_manager.register_order(order)
                del self.active_trades[symbol]
                return order

        # Trailing Stop & Target for SHORT
        elif trade["side"] == OrderSide.SELL:
            # Trailing SL update if price made a new low
            if ltp < trade["entry"]:
                new_sl = self.router.normalize_tick_size(ltp * (1.0 + (self.trailing_sl_pct / 100.0)))
                if new_sl < trade["sl"]:
                    trade["sl"] = new_sl

            # Target or SL Hit
            if ltp <= trade["target"] or ltp >= trade["sl"]:
                exit_reason = "Target Hit 🎯" if ltp <= trade["target"] else "Stop-Loss Hit 🛑"
                logger.info(f"[MTB] {exit_reason} for {symbol} @ ₹{ltp:.2f} (SL: ₹{trade['sl']:.2f}, Target: ₹{trade['target']:.2f})")
                req = self._create_request(symbol, OrderSide.BUY, trade["qty"], ltp)
                order = self.router.route_order(req, lot_size=trade["lot_size"])
                self.order_manager.register_order(order)
                del self.active_trades[symbol]
                return order

        return None

    def _create_request(self, symbol: str, side: OrderSide, quantity: int, price: float) -> OrderRequest:
        import uuid
        from core.models import OrderRequest
        return OrderRequest(
            client_order_id=f"MTB-{uuid.uuid4().hex[:6].upper()}",
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=quantity,
            price=price,
            tag="MTB_Momentum",
        )
