"""
Tests for sector_quant.execution.simulated
"""

from queue import Queue

import pytest

from sector_quant.events import FillEvent, OrderEvent
from sector_quant.execution.simulated import SimulatedExecutionHandler


def test_simulated_execution_handler():
    q = Queue()
    exec_handler = SimulatedExecutionHandler(
        events_queue=q,
        commission_pct=0.0005,
        slippage_pct=0.0005,
        min_commission=1.0,
    )

    order = OrderEvent(
        symbol="HDFCBANK",
        order_type="MKT",
        quantity=100,
        direction="BUY",
        price=1500.0,
    )

    exec_handler.execute_order(order)
    assert not q.empty()

    fill = q.get()
    assert isinstance(fill, FillEvent)
    assert fill.symbol == "HDFCBANK"
    assert fill.quantity == 100
    assert fill.direction == "BUY"
    # Slippage added on BUY: 1500 * (1 + 0.0005) = 1500.75
    assert fill.price_per_share == pytest.approx(1500.75, abs=0.01)
    # Commission: 1500.75 * 100 * 0.0005 = ~75.04
    assert fill.commission > 1.0
