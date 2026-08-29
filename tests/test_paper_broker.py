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


def test_paper_broker_full_trade_cycle_and_margin_release():
    broker = PaperBroker(initial_capital=100000.0, slippage_pct=0.0)
    broker.set_market_price("INFY", 1000.0)

    # 1. Buy 10 INFY @ 1000 (MIS margin required = 10 * 1000 * 0.20 = 2000)
    req_buy = OrderRequest(
        client_order_id="PB-BUY-1",
        symbol="INFY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        product_type=ProductType.MIS,
        price=1000.0,
        quantity=10,
    )
    broker.place_order(req_buy)
    funds1 = broker.get_funds()
    assert funds1.available_margin == 98000.0  # 100,000 - 2000

    # 2. Sell 10 INFY @ 1100 (Profit = +1000, margin released = +2000, total margin = 101,000)
    broker.set_market_price("INFY", 1100.0)
    req_sell = OrderRequest(
        client_order_id="PB-SELL-1",
        symbol="INFY",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        product_type=ProductType.MIS,
        price=1100.0,
        quantity=10,
    )
    broker.place_order(req_sell)
    pos = broker.get_positions()[0]
    assert pos.quantity == 0
    assert pos.realized_pnl == 1000.0
    assert pos.average_buy_price == 0.0

    funds2 = broker.get_funds()
    assert funds2.realized_pnl == 1000.0
    assert funds2.available_margin == 101000.0
    assert funds2.total_capital == 101000.0

    # 3. Buy again at 2000 -> average buy price must be exactly 2000, not skewed by old trade
    broker.set_market_price("INFY", 2000.0)
    req_buy2 = OrderRequest(
        client_order_id="PB-BUY-2",
        symbol="INFY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        product_type=ProductType.MIS,
        price=2000.0,
        quantity=5,
    )
    broker.place_order(req_buy2)
    pos2 = broker.get_positions()[0]
    assert pos2.quantity == 5
    assert pos2.average_buy_price == 2000.0

