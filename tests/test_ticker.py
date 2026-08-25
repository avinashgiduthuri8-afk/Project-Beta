"""
Unit tests for Real-Time Ticker & Candle Resampler (Prompt D).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.enums import Exchange
from core.models import Candle, Tick
from data.candle_builder import CandleBuilder
from data.event_bus import EventBus


def test_candle_builder_aggregation():
    closed_candles = []

    def on_closed(candle: Candle):
        closed_candles.append(candle)

    builder = CandleBuilder(timeframe_minutes=1, on_candle_closed=on_closed)

    base_time = datetime(2026, 8, 26, 10, 0, 5)

    # 3 ticks within the 10:00:00 - 10:01:00 bucket
    t1 = Tick(symbol="TCS", instrument_token=123, last_price=4200.0, last_quantity=5, timestamp=base_time)
    t2 = Tick(symbol="TCS", instrument_token=123, last_price=4215.0, last_quantity=10, timestamp=base_time + timedelta(seconds=15))
    t3 = Tick(symbol="TCS", instrument_token=123, last_price=4195.0, last_quantity=8, timestamp=base_time + timedelta(seconds=30))

    builder.process_tick(t1)
    builder.process_tick(t2)
    builder.process_tick(t3)

    active = builder.get_active_candle("TCS")
    assert active is not None
    assert active.open == 4200.0
    assert active.high == 4215.0
    assert active.low == 4195.0
    assert active.close == 4195.0
    assert active.volume == 23

    # Tick in next minute bucket triggers closing of previous candle
    t4 = Tick(symbol="TCS", instrument_token=123, last_price=4205.0, last_quantity=2, timestamp=base_time + timedelta(seconds=65))
    builder.process_tick(t4)

    assert len(closed_candles) == 1
    assert closed_candles[0].open == 4200.0
    assert closed_candles[0].high == 4215.0
    assert closed_candles[0].low == 4195.0
    assert closed_candles[0].close == 4195.0
    assert closed_candles[0].is_closed is True


def test_event_bus_pub_sub():
    bus = EventBus()
    received_items = []

    def listener(item):
        received_items.append(item)

    bus.subscribe("test_event", listener)
    bus.publish("test_event", {"message": "hello"})

    assert len(received_items) == 1
    assert received_items[0]["message"] == "hello"
