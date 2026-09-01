"""Unit tests for Intra-Sector Pairs Trading and Sector Momentum Strategies."""

import pytest
import numpy as np
import pandas as pd
from sector_quant.strategies.math_utils import (
    rolling_ols,
    calculate_spread_and_zscore,
    estimate_cointegration_half_life,
    calculate_relative_strength,
)
from sector_quant.strategies.pairs_trading import (
    SectorPairsTradingStrategy,
    PairConfig,
    PairPositionState,
)
from sector_quant.strategies.sector_momentum import (
    SectorMomentumStrategy,
    SectorUniverseConfig,
)
from sector_quant.events.queue import EventQueue
from sector_quant.events.events import SignalType
from sector_quant.data.historic_sector import HistoricSectorDataHandler


def test_ols_and_spread_zscore():
    x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    y = 5.0 + 1.5 * x  # alpha=5, beta=1.5
    beta, alpha, r2 = rolling_ols(y, x)
    assert pytest.approx(beta, rel=1e-4) == 1.5
    assert pytest.approx(alpha, rel=1e-4) == 5.0
    assert pytest.approx(r2, rel=1e-4) == 1.0

    x_series = np.linspace(100, 110, 50)
    y_series = 1.2 * x_series + np.random.normal(0, 0.5, 50)
    y_series[-1] += 6.0  # Spike at end

    res = calculate_spread_and_zscore(y_series, x_series, z_lookback=30)
    assert res["beta"] > 0
    assert res["z_score"] > 2.0


def test_cointegration_half_life():
    spread = [0.0]
    for _ in range(200):
        spread.append(spread[-1] + 0.2 * (0 - spread[-1]) + np.random.normal(0, 1))
    hl = estimate_cointegration_half_life(np.array(spread))
    assert 1.0 < hl < 20.0


def test_pairs_trading_strategy_signals():
    queue = EventQueue()
    dates = pd.date_range("2024-01-01", periods=60, freq="B")

    x_prices = 100.0 + np.sin(np.linspace(0, 10, 60)) * 5.0
    y_prices = 1.5 * x_prices.copy()
    y_prices[55] += 15.0  # Spread expansion

    df_x = pd.DataFrame({"date": dates, "open": x_prices, "high": x_prices, "low": x_prices, "close": x_prices, "volume": 1000})
    df_y = pd.DataFrame({"date": dates, "open": y_prices, "high": y_prices, "low": y_prices, "close": y_prices, "volume": 1000})

    handler = HistoricSectorDataHandler(
        events_queue=queue,
        symbol_list=["AREX", "WLL"],
        data_dict={"AREX": df_y, "WLL": df_x},
    )

    pair_cfg = PairConfig(
        symbol_y="AREX",
        symbol_x="WLL",
        sector="ENERGY",
        ols_window=30,
        z_lookback=20,
        z_entry=1.8,
        z_exit=0.4,
    )
    strategy = SectorPairsTradingStrategy(
        bars=handler,
        events_queue=queue,
        pairs=[pair_cfg],
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
    short_sigs = [s for s in signals_emitted if s.symbol == "AREX" and s.signal_type == SignalType.SHORT]
    assert len(short_sigs) > 0


def test_sector_momentum_ranking_and_rotation():
    queue = EventQueue()
    dates = pd.date_range("2024-01-01", periods=40, freq="B")

    bench_prices = np.linspace(100, 105, 40)
    a_prices = np.linspace(50, 75, 40)  # Strong leader (+50%)
    b_prices = np.linspace(50, 50, 40)  # Flat

    df_bench = pd.DataFrame({"date": dates, "open": bench_prices, "high": bench_prices, "low": bench_prices, "close": bench_prices, "volume": 1000})
    df_a = pd.DataFrame({"date": dates, "open": a_prices, "high": a_prices, "low": a_prices, "close": a_prices, "volume": 1000})
    df_b = pd.DataFrame({"date": dates, "open": b_prices, "high": b_prices, "low": b_prices, "close": b_prices, "volume": 1000})

    handler = HistoricSectorDataHandler(
        events_queue=queue,
        symbol_list=["XLK", "AAPL", "MSFT"],
        data_dict={"XLK": df_bench, "AAPL": df_a, "MSFT": df_b},
    )

    sec_cfg = SectorUniverseConfig(
        sector_code="TECH",
        benchmark_symbol="XLK",
        constituents=["AAPL", "MSFT"],
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
    long_a_sigs = [s for s in signals_emitted if s.symbol == "AAPL" and s.signal_type == SignalType.LONG]
    assert len(long_a_sigs) > 0

