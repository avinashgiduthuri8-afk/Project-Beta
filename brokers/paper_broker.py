"""
Paper Trading Simulation Broker.
Provides zero-risk, realistic local execution simulation for Indian Equities and F&O.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from core.enums import Exchange, OrderSide, OrderStatus, OrderType, ProductType
from core.interfaces import BaseBroker
from core.models import AccountBalance, Order, OrderRequest, Position, Trade

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """
    In-memory simulation broker.
    Maintains orders, fills, virtual positions, and margin balances.
    """

    def __init__(self, initial_capital: float = 100000.0) -> None:
        self.initial_capital = initial_capital
        self.available_cash = initial_capital
        self.available_margin = initial_capital
        self.utilized_margin = 0.0

        self._orders: Dict[str, Order] = {}
        self._trades: List[Trade] = []
        self._positions: Dict[str, Position] = {}
        self._market_prices: Dict[str, float] = {
            "RELIANCE": 2950.0,
            "TCS": 4200.0,
            "INFY": 1850.0,
            "HDFCBANK": 1650.0,
            "NIFTY26AUG24500CE": 125.0,
        }
        self._authenticated = True
        logger.info(f"Initialized PaperBroker with ₹{initial_capital:,.2f} virtual margin.")

    def set_market_price(self, symbol: str, price: float) -> None:
        """Update current simulated price for a symbol."""
        self._market_prices[symbol] = price
        # Recalculate unrealized P&L for open positions
        for pos in self._positions.values():
            if pos.symbol == symbol:
                pos.last_price = price
                if pos.quantity > 0:
                    pos.unrealized_pnl = (price - pos.buy_price) * pos.quantity
                elif pos.quantity < 0:
                    pos.unrealized_pnl = (pos.sell_price - price) * abs(pos.quantity)
                else:
                    pos.unrealized_pnl = 0.0
                pos.pnl = pos.realized_pnl + pos.unrealized_pnl

    def authenticate(self) -> bool:
        self._authenticated = True
        logger.info("PaperBroker: Simulated authentication successful.")
        return True

    def is_authenticated(self) -> bool:
        return self._authenticated

    def get_profile(self) -> Dict[str, Any]:
        return {
            "user_id": "PAPER_TRADER_001",
            "user_name": "Project Beta Paper Account",
            "email": "paper@projectbeta.internal",
            "broker": "PaperBroker",
            "exchanges": ["NSE", "BSE", "NFO"],
            "products": ["MIS", "CNC", "NRML"],
        }

    def get_funds(self) -> AccountBalance:
        return AccountBalance(
            available_cash=self.available_cash,
            available_margin=self.available_margin,
            utilized_margin=self.utilized_margin,
            collateral=0.0,
            currency="INR",
            updated_at=datetime.now(timezone.utc),
        )

    def get_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_order_book(self) -> List[Order]:
        return list(self._orders.values())

    def place_order(self, request: OrderRequest) -> Order:
        """Simulate order placement with immediate or pending fill logic."""
        order_id = f"PB_{uuid.uuid4().hex[:8].upper()}"
        exchange_order_id = f"EXCH_{uuid.uuid4().hex[:10].upper()}"

        current_price = self._market_prices.get(request.symbol, request.price or 100.0)
        exec_price = request.price if request.order_type == OrderType.LIMIT else current_price

        # Check margin
        required_margin = (exec_price or current_price) * request.quantity
        if request.product == ProductType.MIS:
            required_margin *= 0.20  # 5x intraday leverage

        if required_margin > self.available_margin and request.side == OrderSide.BUY:
            order = Order(
                order_id=order_id,
                symbol=request.symbol,
                exchange=request.exchange,
                instrument_token=request.instrument_token,
                side=request.side,
                order_type=request.order_type,
                product=request.product,
                quantity=request.quantity,
                filled_quantity=0,
                pending_quantity=request.quantity,
                price=request.price,
                trigger_price=request.trigger_price,
                status=OrderStatus.REJECTED,
                status_message=f"Insufficient margin. Required: ₹{required_margin:,.2f}, Available: ₹{self.available_margin:,.2f}",
                tag=request.tag,
            )
            self._orders[order_id] = order
            logger.warning(f"PaperBroker order rejected: {order.status_message}")
            return order

        # Determine execution
        if request.order_type == OrderType.MARKET:
            fill_price = current_price
            status = OrderStatus.COMPLETE
            filled_qty = request.quantity
            pending_qty = 0
        elif request.order_type == OrderType.LIMIT:
            # If buy limit is >= current price or sell limit is <= current price, fill immediately
            if (request.side == OrderSide.BUY and (request.price or 0) >= current_price) or \
               (request.side == OrderSide.SELL and (request.price or 0) <= current_price):
                fill_price = request.price or current_price
                status = OrderStatus.COMPLETE
                filled_qty = request.quantity
                pending_qty = 0
            else:
                fill_price = 0.0
                status = OrderStatus.OPEN
                filled_qty = 0
                pending_qty = request.quantity
        elif request.order_type in (OrderType.SL, OrderType.SL_M):
            fill_price = 0.0
            status = OrderStatus.TRIGGER_PENDING
            filled_qty = 0
            pending_qty = request.quantity
        else:
            fill_price = current_price
            status = OrderStatus.COMPLETE
            filled_qty = request.quantity
            pending_qty = 0

        order = Order(
            order_id=order_id,
            exchange_order_id=exchange_order_id,
            symbol=request.symbol,
            exchange=request.exchange,
            instrument_token=request.instrument_token,
            side=request.side,
            order_type=request.order_type,
            product=request.product,
            quantity=request.quantity,
            filled_quantity=filled_qty,
            pending_quantity=pending_qty,
            price=request.price,
            trigger_price=request.trigger_price,
            average_price=fill_price if status == OrderStatus.COMPLETE else 0.0,
            status=status,
            status_message="Executed successfully" if status == OrderStatus.COMPLETE else "Order open in simulated exchange",
            tag=request.tag,
        )
        self._orders[order_id] = order

        if status == OrderStatus.COMPLETE:
            self._record_fill(order, fill_price, request.quantity)

        logger.info(f"PaperBroker placed order {order_id}: {order.side} {order.quantity} {order.symbol} @ {order.price or 'MKT'} -> {order.status}")
        return order

    def _record_fill(self, order: Order, fill_price: float, fill_qty: int) -> None:
        """Update positions, trade history and account margins upon a fill."""
        trade = Trade(
            trade_id=f"TRD_{uuid.uuid4().hex[:8].upper()}",
            order_id=order.order_id,
            exchange_order_id=order.exchange_order_id,
            symbol=order.symbol,
            exchange=order.exchange,
            side=order.side,
            product=order.product,
            price=fill_price,
            quantity=fill_qty,
            timestamp=datetime.now(timezone.utc),
        )
        self._trades.append(trade)

        pos_key = f"{order.symbol}_{order.product.value}"
        pos = self._positions.get(pos_key)
        if not pos:
            pos = Position(
                symbol=order.symbol,
                exchange=order.exchange,
                product=order.product,
                quantity=0,
                last_price=fill_price,
            )
            self._positions[pos_key] = pos

        if order.side == OrderSide.BUY:
            # Buying
            total_cost = (pos.buy_price * pos.buy_quantity) + (fill_price * fill_qty)
            pos.buy_quantity += fill_qty
            pos.buy_price = total_cost / pos.buy_quantity
            pos.quantity += fill_qty
            pos.buy_value += fill_price * fill_qty
            # Update margin
            margin_used = (fill_price * fill_qty) * (0.20 if order.product == ProductType.MIS else 1.0)
            self.utilized_margin += margin_used
            self.available_margin = max(0.0, self.available_margin - margin_used)
        else:
            # Selling
            total_sell_val = (pos.sell_price * pos.sell_quantity) + (fill_price * fill_qty)
            pos.sell_quantity += fill_qty
            pos.sell_price = total_sell_val / pos.sell_quantity
            pos.quantity -= fill_qty
            pos.sell_value += fill_price * fill_qty

            if pos.buy_quantity > 0:
                # Realizing P&L for long exit
                realized = (fill_price - pos.buy_price) * min(fill_qty, pos.buy_quantity)
                pos.realized_pnl += realized
                self.available_cash += realized
                self.available_margin += realized
                margin_released = (pos.buy_price * fill_qty) * (0.20 if order.product == ProductType.MIS else 1.0)
                self.utilized_margin = max(0.0, self.utilized_margin - margin_released)
                self.available_margin += margin_released

        pos.last_price = fill_price
        pos.pnl = pos.realized_pnl + pos.unrealized_pnl
        pos.updated_at = datetime.now(timezone.utc)

    def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        quantity: Optional[int] = None,
    ) -> Order:
        order = self._orders.get(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found in PaperBroker")
        if order.status not in (OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING):
            raise ValueError(f"Cannot modify order in status {order.status}")

        if price is not None:
            order.price = price
        if trigger_price is not None:
            order.trigger_price = trigger_price
        if quantity is not None:
            order.quantity = quantity
            order.pending_quantity = quantity - order.filled_quantity

        order.updated_at = datetime.now(timezone.utc)
        logger.info(f"PaperBroker modified order {order_id}: Price={order.price}, Qty={order.quantity}")
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order:
            logger.warning(f"Order {order_id} not found to cancel")
            return False
        if order.status in (OrderStatus.COMPLETE, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            logger.warning(f"Order {order_id} already in terminal state {order.status}")
            return False

        order.status = OrderStatus.CANCELLED
        order.pending_quantity = 0
        order.status_message = "Cancelled by user/system"
        order.updated_at = datetime.now(timezone.utc)
        logger.info(f"PaperBroker cancelled order {order_id}")
        return True
