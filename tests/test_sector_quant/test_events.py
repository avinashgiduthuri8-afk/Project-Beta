"""Unit tests for Core Event Objects and Event Queue."""

import pytest
from datetime import datetime
from sector_quant.events.events import (
    EventType,
    SignalType,
    OrderType,
    OrderDirection,
    MarketEvent,
    SignalEvent,
    OrderEvent,
    FillEvent,
)
from sector_quant.events.queue import EventQueue


def test_market_event_creation():
    dt = datetime(2024, 1, 15, 9, 30)
    event = MarketEvent(datetime=dt, symbol="AAPL")
    assert event.type == EventType.MARKET
    assert event.datetime == dt
    assert event.symbol == "AAPL"


def test_signal_event_creation():
    dt = datetime(2024, 1, 15, 9, 30)
    sig = SignalEvent(
        strategy_id="PAIRS_01",
        symbol="XOM",
        datetime=dt,
        signal_type=SignalType.LONG,
        strength=1.35,
        meta={"pair": "CVX"},
    )
    assert sig.type == EventType.SIGNAL
    assert sig.symbol == "XOM"
    assert sig.signal_type == SignalType.LONG
    assert sig.strength == 1.35
    assert sig.meta["pair"] == "CVX"


def test_order_event_creation():
    dt = datetime(2024, 1, 15, 9, 30)
    order = OrderEvent(
        symbol="XOM",
        order_type=OrderType.MKT,
        quantity=100,
        direction=OrderDirection.BUY,
        datetime=dt,
        price=105.50,
    )
    assert order.type == EventType.ORDER
    assert order.quantity == 100
    assert order.direction == OrderDirection.BUY
    assert order.price == 105.50
    assert "XOM" in repr(order)


def test_fill_event_creation():
    dt = datetime(2024, 1, 15, 9, 30)
    fill = FillEvent(
        timeindex=dt,
        symbol="XOM",
        exchange="NYSE",
        quantity=100,
        direction=OrderDirection.BUY,
        fill_price=105.55,
        fill_cost=10555.0,
        commission=1.0,
        slippage=5.0,
    )
    assert fill.type == EventType.FILL
    assert fill.quantity == 100
    assert fill.commission == 1.0
    assert fill.slippage == 5.0


def test_event_queue_fifo_and_counts():
    queue = EventQueue()
    assert queue.empty() is True

    dt = datetime(2024, 1, 15)
    e1 = MarketEvent(datetime=dt)
    e2 = SignalEvent(strategy_id="S1", symbol="AAPL", datetime=dt, signal_type=SignalType.LONG)
    e3 = OrderEvent(symbol="AAPL", order_type=OrderType.MKT, quantity=50, direction=OrderDirection.BUY)

    queue.put(e1)
    queue.put(e2)
    queue.put(e3)

    assert queue.qsize() == 3
    assert queue.event_counts[EventType.MARKET] == 1
    assert queue.event_counts[EventType.SIGNAL] == 1
    assert queue.event_counts[EventType.ORDER] == 1

    out1 = queue.get()
    out2 = queue.get()
    out3 = queue.get()

    assert out1.type == EventType.MARKET
    assert out2.type == EventType.SIGNAL
    assert out3.type == EventType.ORDER
    assert queue.empty() is True


def test_event_queue_subscriber():
    queue = EventQueue()
    received_signals = []

    def signal_listener(event):
        received_signals.append(event)

    queue.subscribe(EventType.SIGNAL, signal_listener)

    dt = datetime(2024, 1, 15)
    queue.put(MarketEvent(datetime=dt))
    queue.put(SignalEvent(strategy_id="S1", symbol="MSFT", datetime=dt, signal_type=SignalType.SHORT))

    assert len(received_signals) == 1
    assert received_signals[0].symbol == "MSFT"

