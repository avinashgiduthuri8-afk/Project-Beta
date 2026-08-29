"""MRB (Mean Reversion Bot) for Indian Equities & Derivatives (NSE/BSE)."""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional
from core.models import Tick, Candle, Order
from core.enums import OrderSide, OrderType, ProductType, Exchange, OrderStatus
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from risk.position_sizer import PositionSizer
from risk.market_clock import MarketClock

logger = logging.getLogger("Execution.MRB")


class MeanReversionBot:
    """
    MRB: Counter-trend and range-bound mean reversion execution bot.
    - Extended VWAP / Bollinger Band fade executions
    - Bracket orders with fixed scalping target & tight stop-loss
    - Time-stop safety exit if price does not revert within N periods
    """

    def __init__(
        self,
        router: ExecutionRouter,
        order_manager: OrderManager,
        market_clock: Optional[MarketClock] = None,
        deviation_threshold_pct: float = 1.2,
        target_pct: float = 0.8,
        stop_loss_pct: float = 0.6,
        max_holding_candles: int = 6,
    ):
        self.router = router
        self.order_manager = order_manager
        self.market_clock = market_clock or MarketClock()
        self.deviation_threshold_pct = deviation_threshold_pct
        self.target_pct = target_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_holding_candles = max_holding_candles
        self._lock = threading.Lock()

        # Tracking state: symbol -> {"entry": float, "sl": float, "target": float, "side": OrderSide, "qty": int, "bars_held": int}
        self.active_trades: Dict[str, Dict] = {}

    def reset(self) -> None:
        """Clear active trade state."""
        with self._lock:
            self.active_trades.clear()
            logger.info("[MRB] Active trades reset.")

    def on_candle(self, candle: Candle, capital: float = 100000.0, lot_size: int = 1) -> Optional[Order]:
        """Process candle for mean reversion fade opportunities & manage bar holding time."""
        if not candle.is_closed or not candle.vwap or candle.vwap <= 0:
            return None

        if not self.market_clock.is_normal_trading_active():
            return None

        symbol = candle.symbol

        # Increment bar count for open trades & execute time stop
        with self._lock:
            if symbol in self.active_trades:
                trade = self.active_trades[symbol]
                trade["bars_held"] += 1
                if trade["bars_held"] >= self.max_holding_candles:
                    logger.info(f"[MRB] ⏱ Time-Stop Reached for {symbol} ({trade['bars_held']} bars). Squaring off...")
                    exit_side = OrderSide.SELL if trade["side"] == OrderSide.BUY else OrderSide.BUY
                    req = self._create_request(symbol, exit_side, trade["qty"], candle.close)
                    order = self.router.route_order(req, lot_size=trade["lot_size"])
                    self.order_manager.register_order(order)
                    self.active_trades.pop(symbol, None)
                    return order
                return None

        # Calculate deviation from VWAP
        pct_diff = ((candle.close - candle.vwap) / candle.vwap) * 100.0

        # 1. Overbought Extreme -> FADE SHORT (Expect price to revert back down to VWAP)
        if pct_diff >= self.deviation_threshold_pct:
            entry_price = self.router.normalize_tick_size(candle.close)
            sl_price = self.router.normalize_tick_size(entry_price * (1.0 + (self.stop_loss_pct / 100.0)))
            target_price = self.router.normalize_tick_size(entry_price * (1.0 - (self.target_pct / 100.0)))

            quantity = PositionSizer.calculate_quantity(
                capital=capital,
                risk_per_trade_pct=0.8,
                entry_price=entry_price,
                stop_loss_price=sl_price,
                lot_size=lot_size,
            )

            if quantity <= 0:
                return None

            logger.info(f"[MRB] ⚡ Overbought VWAP Fade on {symbol} (+{pct_diff:.2f}%): Short @ ₹{entry_price:.2f}, Target=₹{target_price:.2f}, SL=₹{sl_price:.2f}")
            req = self._create_request(symbol, OrderSide.SELL, quantity, entry_price)
            order = self.router.route_order(req, lot_size=lot_size)
            self.order_manager.register_order(order)

            if order.status in (OrderStatus.COMPLETE, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING):
                with self._lock:
                    self.active_trades[symbol] = {
                        "entry": entry_price,
                        "sl": sl_price,
                        "target": target_price,
                        "side": OrderSide.SELL,
                        "qty": quantity,
                        "lot_size": lot_size,
                        "bars_held": 0,
                    }
            return order

        # 2. Oversold Extreme -> FADE LONG (Expect price to revert back up to VWAP)
        elif pct_diff <= -self.deviation_threshold_pct:
            entry_price = self.router.normalize_tick_size(candle.close)
            sl_price = self.router.normalize_tick_size(entry_price * (1.0 - (self.stop_loss_pct / 100.0)))
            target_price = self.router.normalize_tick_size(entry_price * (1.0 + (self.target_pct / 100.0)))

            quantity = PositionSizer.calculate_quantity(
                capital=capital,
                risk_per_trade_pct=0.8,
                entry_price=entry_price,
                stop_loss_price=sl_price,
                lot_size=lot_size,
            )

            if quantity <= 0:
                return None

            logger.info(f"[MRB] ⚡ Oversold VWAP Fade on {symbol} ({pct_diff:.2f}%): Long @ ₹{entry_price:.2f}, Target=₹{target_price:.2f}, SL=₹{sl_price:.2f}")
            req = self._create_request(symbol, OrderSide.BUY, quantity, entry_price)
            order = self.router.route_order(req, lot_size=lot_size)
            self.order_manager.register_order(order)

            if order.status in (OrderStatus.COMPLETE, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING):
                with self._lock:
                    self.active_trades[symbol] = {
                        "entry": entry_price,
                        "sl": sl_price,
                        "target": target_price,
                        "side": OrderSide.BUY,
                        "qty": quantity,
                        "lot_size": lot_size,
                        "bars_held": 0,
                    }
            return order

        return None

    def on_tick(self, tick: Tick) -> Optional[Order]:
        """Check target / SL triggers on live ticks."""
        symbol = tick.symbol
        with self._lock:
            if symbol not in self.active_trades:
                return None
            trade = dict(self.active_trades[symbol])

        ltp = tick.ltp

        if trade["side"] == OrderSide.BUY:
            if ltp >= trade["target"] or ltp <= trade["sl"]:
                exit_msg = "Reversion Target Reached 🎯" if ltp >= trade["target"] else "Stop-Loss Hit 🛑"
                logger.info(f"[MRB] {exit_msg} on {symbol} @ ₹{ltp:.2f}")
                req = self._create_request(symbol, OrderSide.SELL, trade["qty"], ltp)
                order = self.router.route_order(req, lot_size=trade["lot_size"])
                self.order_manager.register_order(order)
                with self._lock:
                    self.active_trades.pop(symbol, None)
                return order

        elif trade["side"] == OrderSide.SELL:
            if ltp <= trade["target"] or ltp >= trade["sl"]:
                exit_msg = "Reversion Target Reached 🎯" if ltp <= trade["target"] else "Stop-Loss Hit 🛑"
                logger.info(f"[MRB] {exit_msg} on {symbol} @ ₹{ltp:.2f}")
                req = self._create_request(symbol, OrderSide.BUY, trade["qty"], ltp)
                order = self.router.route_order(req, lot_size=trade["lot_size"])
                self.order_manager.register_order(order)
                with self._lock:
                    self.active_trades.pop(symbol, None)
                return order

        return None

    def _create_request(self, symbol: str, side: OrderSide, quantity: int, price: float) -> OrderRequest:
        import uuid
        from core.models import OrderRequest
        return OrderRequest(
            client_order_id=f"MRB-{uuid.uuid4().hex[:6].upper()}",
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=quantity,
            price=price,
            tag="MRB_MeanReversion",
        )

