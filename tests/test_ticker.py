"""Unit tests for Candle Builder and Event Bus."""

from datetime import datetime
import pytest
from core.models import Tick
from core.enums import Exchange
from data.candle_builder import CandleBuilder
from data.event_bus import EventBus


def test_candle_builder_aggregation():
    closed_candles = []
    builder = CandleBuilder(timeframe_minutes=1, on_candle_close=lambda c: closed_candles.append(c))

    # Ticks within 10:00:00 - 10:00:59 with cumulative exchange volume
    t1 = Tick(token="1", symbol="INFY", ltp=1800.0, volume=10, timestamp=datetime(2026, 8, 26, 10, 0, 5))
    t2 = Tick(token="1", symbol="INFY", ltp=1810.0, volume=25, timestamp=datetime(2026, 8, 26, 10, 0, 30))
    t3 = Tick(token="1", symbol="INFY", ltp=1795.0, volume=45, timestamp=datetime(2026, 8, 26, 10, 0, 55))

    builder.process_tick(t1)
    builder.process_tick(t2)
    builder.process_tick(t3)

    # Next minute tick closes previous candle
    t4 = Tick(token="1", symbol="INFY", ltp=1805.0, volume=55, timestamp=datetime(2026, 8, 26, 10, 1, 5))
    closed = builder.process_tick(t4)

    assert closed is not None
    assert closed.open == 1800.0
    assert closed.high == 1810.0
    assert closed.low == 1795.0
    assert closed.close == 1795.0
    assert closed.volume == 45
    assert len(closed_candles) == 1



def test_event_bus_pub_sub(event_bus):
    received = []
    event_bus.subscribe("test.topic", lambda data: received.append(data))

    event_bus.publish("test.topic", {"msg": "hello"})
    assert len(received) == 1
    assert received[0]["msg"] == "hello"
