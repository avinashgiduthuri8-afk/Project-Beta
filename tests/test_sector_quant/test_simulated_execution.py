"""Unit tests for Simulated Execution Handler."""

import pytest
import pandas as pd
from datetime import datetime
from sector_quant.events.queue import EventQueue
from sector_quant.events.events import OrderEvent, OrderType, OrderDirection, EventType
from sector_quant.execution.simulated import SimulatedExecutionHandler, ExecutionCostConfig, CommissionScheme
from sector_quant.data.historic_sector import HistoricSectorDataHandler


def test_simulated_execution_fills_and_costs():
    queue = EventQueue()
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    df = pd.DataFrame({
        "date": dates,
        "open": [100.0] * 5,
        "high": [105.0] * 5,
        "low": [98.0] * 5,
        "close": [100.0] * 5,
        "volume": [1000] * 5,
    })

    handler = HistoricSectorDataHandler(
        events_queue=queue,
        symbol_list=["AAPL"],
        data_dict={"AAPL": df},
    )
    handler.update_bars()  # Drip-feed 1 bar

    cost_cfg = ExecutionCostConfig(
        commission_scheme=CommissionScheme.PER_SHARE,
        per_share_rate=0.01,
        min_commission=1.0,
        slippage_bps=10.0,  # 10 bps = 0.1% -> Buy fill = 100 * 1.001 = 100.10
    )
    exec_handler = SimulatedExecutionHandler(
        events_queue=queue,
        bars=handler,
        cost_config=cost_cfg,
    )

    # Empty queue of MarketEvent
    queue.get()

    order = OrderEvent(
        symbol="AAPL",
        order_type=OrderType.MKT,
        quantity=100,
        direction=OrderDirection.BUY,
        price=100.0,
    )
    exec_handler.execute_order(order)

    assert queue.qsize() == 1
    fill = queue.get()
    assert fill.type == EventType.FILL
    assert fill.symbol == "AAPL"
    assert pytest.approx(fill.fill_price, rel=1e-3) == 100.10
    assert fill.commission == 1.0  # max(1.0, 100 * 0.01) = 1.0

