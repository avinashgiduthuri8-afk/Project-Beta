"""Unit tests for HistoricSectorDataHandler."""

import pytest
import pandas as pd
from datetime import datetime
from sector_quant.events.queue import EventQueue
from sector_quant.data.historic_sector import HistoricSectorDataHandler


def test_historic_sector_data_handler_drip_feed():
    queue = EventQueue()
    dates = pd.date_range("2024-01-01", periods=10, freq="B")

    df_a = pd.DataFrame({
        "date": dates,
        "open": [10.0 + i for i in range(10)],
        "high": [11.0 + i for i in range(10)],
        "low": [9.0 + i for i in range(10)],
        "close": [10.5 + i for i in range(10)],
        "volume": [1000] * 10,
    })
    df_b = pd.DataFrame({
        "date": dates,
        "open": [20.0 + i * 2 for i in range(10)],
        "high": [22.0 + i * 2 for i in range(10)],
        "low": [19.0 + i * 2 for i in range(10)],
        "close": [21.0 + i * 2 for i in range(10)],
        "volume": [2000] * 10,
    })

    handler = HistoricSectorDataHandler(
        events_queue=queue,
        symbol_list=["STOCK_A", "STOCK_B"],
        data_dict={"STOCK_A": df_a, "STOCK_B": df_b},
    )

    assert handler.total_bars() == 10
    assert handler.continue_backtest is True

    # First update_bars: 1 bar drip-fed
    has_more = handler.update_bars()
    assert has_more is True
    assert queue.qsize() == 1

    bar_a = handler.get_latest_bar("STOCK_A")
    assert bar_a["close"] == 10.5
    bar_b = handler.get_latest_bar("STOCK_B")
    assert bar_b["close"] == 21.0

    # Advance 4 more bars (total 5)
    for _ in range(4):
        handler.update_bars()

    assert handler.current_bar_idx() == 5
    closes_a = handler.get_latest_bars_values("STOCK_A", val_type="close", N=5)
    assert len(closes_a) == 5
    assert closes_a[-1] == 14.5

    # Advance remaining bars to the end
    while handler.continue_backtest:
        handler.update_bars()

    assert handler.continue_backtest is False
    assert handler.current_bar_idx() == 10

