"""Unit tests for Sector Momentum Strategy."""

import pytest
import numpy as np
import pandas as pd
from sector_quant.events.queue import EventQueue
from sector_quant.events.events import SignalType
from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.strategies.sector_momentum import SectorMomentumStrategy, SectorUniverseConfig
from sector_quant.strategies.math_utils import calculate_relative_strength


def test_calculate_relative_strength():
    bench = np.array([100.0, 102.0, 104.0, 105.0, 105.0])  # +5%
    outperformer = np.array([50.0, 52.0, 55.0, 58.0, 60.0])  # +20%
    underperformer = np.array([50.0, 50.0, 49.0, 48.0, 48.0])  # -4%

    rs_out = calculate_relative_strength(outperformer, bench, lookback=4)
    rs_under = calculate_relative_strength(underperformer, bench, lookback=4)

    assert rs_out > 0.10
    assert rs_under < -0.05


def test_sector_momentum_rotation():
    queue = EventQueue()
    dates = pd.date_range("2024-01-01", periods=40, freq="B")

    # Benchmark: modest drift
    bench_prices = np.linspace(100, 105, 40)
    # Stock A: strong outperformance
    a_prices = np.linspace(50, 75, 40)
    # Stock B: flat
    b_prices = np.linspace(50, 50, 40)

    df_bench = pd.DataFrame({"date": dates, "open": bench_prices, "high": bench_prices, "low": bench_prices, "close": bench_prices, "volume": 1000})
    df_a = pd.DataFrame({"date": dates, "open": a_prices, "high": a_prices, "low": a_prices, "close": a_prices, "volume": 1000})
    df_b = pd.DataFrame({"date": dates, "open": b_prices, "high": b_prices, "low": b_prices, "close": b_prices, "volume": 1000})

    handler = HistoricSectorDataHandler(
        events_queue=queue,
        symbol_list=["XLK", "STOCK_A", "STOCK_B"],
        data_dict={"XLK": df_bench, "STOCK_A": df_a, "STOCK_B": df_b},
    )

    sec_cfg = SectorUniverseConfig(
        sector_code="TECH",
        benchmark_symbol="XLK",
        constituents=["STOCK_A", "STOCK_B"],
        lookback_window=10,
        top_n=1,
        min_relative_strength=0.01,
        rebalance_frequency=2,
    )
    strategy = SectorMomentumStrategy(
        bars=handler,
        events_queue=queue,
        sector_config=sec_cfg,
    )

    signals_emitted = []
    while handler.continue_backtest:
        if handler.update_bars():
            market_event = queue.get()
            strategy.calculate_signals(market_event)
            while not queue.empty():
                sig = queue.get()
                signals_emitted.append(sig)

    assert len(signals_emitted) > 0
    long_a_sigs = [s for s in signals_emitted if s.symbol == "STOCK_A" and s.signal_type == SignalType.LONG]
    assert len(long_a_sigs) > 0

