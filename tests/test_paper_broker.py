"""
Unit tests for Paper Broker Simulation.
"""

from __future__ import annotations

from core.enums import Exchange, OrderSide, OrderStatus, OrderType, ProductType
from core.models import OrderRequest
from brokers.paper_broker import PaperBroker


def test_paper_broker_order_execution():
    broker = PaperBroker(initial_capital=50000.0)
    broker.set_market_price("INFY", 1850.0)

    # Place Market Order
    req = OrderRequest(
        symbol="INFY",
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.MIS,
        quantity=10,
    )
    order = broker.place_order(req)
    assert order.status == OrderStatus.COMPLETE
    assert order.average_price == 1850.0

    # Verify positions
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "INFY"
    assert positions[0].quantity == 10
    assert positions[0].buy_price == 1850.0

    # Price moves up to 1860
    broker.set_market_price("INFY", 1860.0)
    assert broker.get_positions()[0].unrealized_pnl == 100.0  # (1860 - 1850) * 10

    # Exit position
    req_exit = OrderRequest(
        symbol="INFY",
        exchange=Exchange.NSE,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        product=ProductType.MIS,
        quantity=10,
    )
    exit_order = broker.place_order(req_exit)
    assert exit_order.status == OrderStatus.COMPLETE
    pos = broker.get_positions()[0]
    assert pos.quantity == 0
    assert pos.realized_pnl == 100.0
