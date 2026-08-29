"""Simulated Paper Broker for Indian Equities and Derivatives."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

from core.interfaces import BaseBroker
from core.enums import OrderStatus, OrderSide, OrderType, ProductType, Exchange
from core.models import OrderRequest, Order, Position, AccountBalance, Trade

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """High-fidelity simulated broker with realistic margin, slippage, and P&L tracking."""

    def __init__(self, initial_capital: float = 100000.0, slippage_pct: float = 0.05):
        self.initial_capital = initial_capital
        self.available_margin = initial_capital
        self.slippage_pct = slippage_pct
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self._lock = threading.RLock()
        self.market_prices: Dict[str, float] = {
            "RELIANCE": 2850.00,
            "INFY": 1820.00,
            "TCS": 4150.00,
            "NIFTY26AUGFUT": 24500.00,
        }
        logger.info(f"Initialized PaperBroker with ₹{initial_capital:,.2f} virtual margin.")

    def set_market_price(self, symbol: str, price: float) -> None:
        """Update simulated market price for execution and position valuation."""
        with self._lock:
            self.market_prices[symbol] = price
            self._update_position_pnls()

    def authenticate(self) -> bool:
        logger.info("PaperBroker: Simulated authentication successful.")
        return True

    def get_profile(self) -> Dict[str, Any]:
        return {
            "user_id": "PAPER_TRADER_01",
            "user_name": "Project Beta Paper Trader",
            "broker": "PaperBroker",
            "status": "ACTIVE",
        }

    def _margin_for_value(self, value: float, product_type: ProductType) -> float:
        if product_type == ProductType.CNC:
            return value
        elif product_type == ProductType.NRML:
            return value * 0.25
        return value * 0.20  # MIS 5x leverage

    def get_funds(self) -> AccountBalance:
        with self._lock:
            self._update_position_pnls()
            realized_pnl = sum(p.realized_pnl for p in self.positions.values())
            unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
            total_pnl = realized_pnl + unrealized_pnl
            total_capital = self.initial_capital + total_pnl
            utilized_margin = max(0.0, total_capital - self.available_margin)

            return AccountBalance(
                total_capital=total_capital,
                available_margin=self.available_margin,
                utilized_margin=utilized_margin,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
            )

    def get_positions(self) -> List[Position]:
        with self._lock:
            self._update_position_pnls()
            return list(self.positions.values())

    def get_orders(self) -> List[Order]:
        with self._lock:
            return list(self.orders.values())

    def place_order(self, request: OrderRequest) -> Order:
        with self._lock:
            order_id = f"PB-{uuid.uuid4().hex[:8].upper()}"
            current_ltp = self.market_prices.get(request.symbol, request.price or 100.0)

            # Apply slippage simulation
            slippage_factor = (self.slippage_pct / 100.0) if request.order_type == OrderType.MARKET else 0.0
            if request.side == OrderSide.BUY:
                fill_price = round(current_ltp * (1.0 + slippage_factor), 2)
            else:
                fill_price = round(current_ltp * (1.0 - slippage_factor), 2)

            if request.price is not None and request.order_type == OrderType.LIMIT:
                fill_price = request.price

            order = Order(
                order_id=order_id,
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                exchange=request.exchange,
                side=request.side,
                order_type=request.order_type,
                product_type=request.product_type,
                quantity=request.quantity,
                filled_quantity=request.quantity,
                pending_quantity=0,
                price=request.price,
                average_price=fill_price,
                trigger_price=request.trigger_price,
                status=OrderStatus.COMPLETE,
                status_message="Filled by PaperBroker",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            self.orders[order_id] = order

            # Create Trade execution
            trade = Trade(
                trade_id=f"TRD-{uuid.uuid4().hex[:8].upper()}",
                order_id=order_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                price=fill_price,
                value=fill_price * request.quantity,
                timestamp=datetime.now(),
            )
            self.trades.append(trade)

            # Update in-memory positions & margin
            self._record_trade(trade, request.exchange, request.product_type)
            logger.info(f"PaperBroker Executed: {request.side.value} {request.quantity}x {request.symbol} @ ₹{fill_price:.2f}")
            return order

    def cancel_order(self, order_id: str) -> bool:
        with self._lock:
            if order_id in self.orders:
                order = self.orders[order_id]
                if order.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING):
                    order.status = OrderStatus.CANCELLED
                    order.updated_at = datetime.now()
                    return True
            return False

    def modify_order(self, order_id: str, quantity: Optional[int] = None, price: Optional[float] = None, trigger_price: Optional[float] = None) -> Order:
        with self._lock:
            if order_id not in self.orders:
                raise ValueError(f"Order {order_id} not found.")
            order = self.orders[order_id]
            if quantity:
                order.quantity = quantity
            if price:
                order.price = price
            if trigger_price:
                order.trigger_price = trigger_price
            order.updated_at = datetime.now()
            return order

    def _record_trade(self, trade: Trade, exchange: Exchange, product_type: ProductType) -> None:
        pos_key = f"{trade.symbol}_{product_type.value}"
        if pos_key not in self.positions:
            self.positions[pos_key] = Position(
                symbol=trade.symbol,
                exchange=exchange,
                product_type=product_type,
                quantity=0,
                ltp=trade.price,
            )

        pos = self.positions[pos_key]
        pos.ltp = trade.price

        if trade.side == OrderSide.BUY:
            if pos.quantity < 0:
                # Closing short position
                closed_qty = min(abs(pos.quantity), trade.quantity)
                realized = (pos.average_sell_price - trade.price) * closed_qty
                pos.realized_pnl += realized
                released_margin = self._margin_for_value(closed_qty * pos.average_sell_price, product_type)
                self.available_margin += released_margin + realized

                remaining_qty = trade.quantity - closed_qty
                new_pos_qty = pos.quantity + trade.quantity

                if new_pos_qty == 0:
                    pos.quantity = 0
                    pos.buy_quantity = 0
                    pos.buy_value = 0.0
                    pos.sell_quantity = 0
                    pos.sell_value = 0.0
                    pos.average_buy_price = 0.0
                    pos.average_sell_price = 0.0
                elif new_pos_qty > 0:
                    pos.quantity = new_pos_qty
                    pos.buy_quantity = remaining_qty
                    pos.buy_value = remaining_qty * trade.price
                    pos.average_buy_price = trade.price
                    pos.sell_quantity = 0
                    pos.sell_value = 0.0
                    pos.average_sell_price = 0.0
                    self.available_margin -= self._margin_for_value(pos.buy_value, product_type)
                else:
                    pos.quantity = new_pos_qty
            else:
                # Opening or adding to long position
                pos.buy_quantity += trade.quantity
                pos.buy_value += trade.value
                pos.average_buy_price = pos.buy_value / pos.buy_quantity if pos.buy_quantity > 0 else 0.0
                pos.quantity += trade.quantity
                self.available_margin -= self._margin_for_value(trade.value, product_type)

        else:  # SELL
            if pos.quantity > 0:
                # Closing long position
                closed_qty = min(pos.quantity, trade.quantity)
                realized = (trade.price - pos.average_buy_price) * closed_qty
                pos.realized_pnl += realized
                released_margin = self._margin_for_value(closed_qty * pos.average_buy_price, product_type)
                self.available_margin += released_margin + realized

                remaining_qty = trade.quantity - closed_qty
                new_pos_qty = pos.quantity - trade.quantity

                if new_pos_qty == 0:
                    pos.quantity = 0
                    pos.buy_quantity = 0
                    pos.buy_value = 0.0
                    pos.sell_quantity = 0
                    pos.sell_value = 0.0
                    pos.average_buy_price = 0.0
                    pos.average_sell_price = 0.0
                elif new_pos_qty < 0:
                    pos.quantity = new_pos_qty
                    pos.sell_quantity = remaining_qty
                    pos.sell_value = remaining_qty * trade.price
                    pos.average_sell_price = trade.price
                    pos.buy_quantity = 0
                    pos.buy_value = 0.0
                    pos.average_buy_price = 0.0
                    self.available_margin -= self._margin_for_value(pos.sell_value, product_type)
                else:
                    pos.quantity = new_pos_qty
            else:
                # Opening or adding to short position
                pos.sell_quantity += trade.quantity
                pos.sell_value += trade.value
                pos.average_sell_price = pos.sell_value / pos.sell_quantity if pos.sell_quantity > 0 else 0.0
                pos.quantity -= trade.quantity
                self.available_margin -= self._margin_for_value(trade.value, product_type)

        self._update_position_pnls()

    def _update_position_pnls(self) -> None:
        for pos in self.positions.values():
            ltp = self.market_prices.get(pos.symbol, pos.ltp)
            pos.ltp = ltp
            if pos.quantity > 0:
                pos.unrealized_pnl = (ltp - pos.average_buy_price) * pos.quantity
            elif pos.quantity < 0:
                pos.unrealized_pnl = (pos.average_sell_price - ltp) * abs(pos.quantity)
            else:
                pos.unrealized_pnl = 0.0
            pos.total_pnl = pos.realized_pnl + pos.unrealized_pnl

