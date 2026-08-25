"""Unit tests for Paper Broker simulation engine."""

import pytest
from core.enums import OrderSide, OrderType, ProductType, Exchange, OrderStatus
from core.models import OrderRequest
from brokers.paper_broker import PaperBroker


def test_paper_broker_order_execution():
    broker = PaperBroker(initial_capital=100000.0, slippage_pct=0.0)
    broker.set_market_price("RELIANCE", 2500.0)

    req = OrderRequest(
        client_order_id="PB-TEST-1",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.MIS,
        quantity=10,
    )
    order = broker.place_order(req)
    assert order.status == OrderStatus.COMPLETE
    assert order.average_price == 2500.0

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "RELIANCE"
    assert positions[0].quantity == 10
    assert positions[0].average_buy_price == 2500.0

    # Test unrealized P&L when price increases to 2550
    broker.set_market_price("RELIANCE", 2550.0)
    funds = broker.get_funds()
    assert funds.unrealized_pnl == 500.0 # (2550 - 2500) * 10
