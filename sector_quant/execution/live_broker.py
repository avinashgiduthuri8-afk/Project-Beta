"""Live Broker Integration Handler (Interactive Brokers / TWS & Adapters)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional, Callable

from sector_quant.execution.base import ExecutionHandler
from sector_quant.events.events import OrderEvent, FillEvent, OrderDirection
from sector_quant.events.queue import EventQueue

logger = logging.getLogger(__name__)


class BrokerClientInterface(ABC):
    """Abstract interface for native broker client APIs (e.g. IB API, Kite, etc.)."""

    @abstractmethod
    def connect(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, symbol: str, quantity: int, side: str, order_type: str, price: Optional[float] = None) -> str:
        raise NotImplementedError


class MockInteractiveBrokersClient(BrokerClientInterface):
    """Mock Interactive Brokers TWS/Gateway client for paper trading and live interface verification."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._connected = False
        self._order_id = 1000

    def connect(self) -> bool:
        logger.info(f"Connecting to Interactive Brokers TWS at {self.host}:{self.port} (Client ID: {self.client_id})")
        self._connected = True
        return True

    def disconnect(self) -> None:
        logger.info("Disconnected from Interactive Brokers TWS")
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def place_order(self, symbol: str, quantity: int, side: str, order_type: str, price: Optional[float] = None) -> str:
        self._order_id += 1
        ib_order_id = f"IB_ORD_{self._order_id}"
        logger.info(f"IB Order Placed: {ib_order_id} -> {side} {quantity} {symbol} @ {order_type} {price or 'MKT'}")
        return ib_order_id


class LiveBrokerExecutionHandler(ExecutionHandler):
    """Execution handler bridging OrderEvents to live/paper broker connections."""

    def __init__(
        self,
        events_queue: EventQueue,
        broker_client: Optional[BrokerClientInterface] = None,
        exchange: str = "SMART",
    ):
        super().__init__(events_queue=events_queue)
        self.client = broker_client or MockInteractiveBrokersClient()
        self.exchange = exchange
        self.pending_orders: Dict[str, OrderEvent] = {}

    def connect(self) -> bool:
        return self.client.connect()

    def disconnect(self) -> None:
        self.client.disconnect()

    def execute_order(self, event: OrderEvent) -> None:
        """Transmits OrderEvent to live broker and sets up fill listener."""
        if not self.client.is_connected():
            self.client.connect()

        symbol = event.symbol.upper()
        side = event.direction.value
        qty = event.quantity
        order_type = event.order_type.value
        price = event.price

        try:
            broker_order_id = self.client.place_order(
                symbol=symbol,
                quantity=qty,
                side=side,
                order_type=order_type,
                price=price,
            )
            self.pending_orders[broker_order_id] = event

            # In synchronous mock / broker callback simulation:
            # Map immediate execution fill response back to central EventQueue
            self.on_broker_execution_fill(
                broker_order_id=broker_order_id,
                symbol=symbol,
                quantity=qty,
                fill_price=price or 100.0,
                direction=event.direction,
                commission=max(1.0, qty * 0.005),
                timeindex=event.datetime or datetime.now(),
            )
        except Exception as e:
            logger.error(f"Live broker order submission failed for {symbol}: {e}", exc_info=True)

    def on_broker_execution_fill(
        self,
        broker_order_id: str,
        symbol: str,
        quantity: int,
        fill_price: float,
        direction: OrderDirection,
        commission: float,
        timeindex: datetime,
    ) -> None:
        """Callback invoked when broker sends execution / fill report."""
        order_event = self.pending_orders.pop(broker_order_id, None)

        fill = FillEvent(
            timeindex=timeindex,
            symbol=symbol,
            exchange=self.exchange,
            quantity=quantity,
            direction=direction,
            fill_price=fill_price,
            fill_cost=quantity * fill_price,
            commission=commission,
            slippage=0.0,
            order_id=broker_order_id,
            strategy_id=order_event.strategy_id if order_event else None,
            sector=order_event.sector if order_event else None,
            meta=order_event.meta if order_event else {},
        )
        self.events_queue.put(fill)

