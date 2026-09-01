"""Unit tests for Pairs Trading Strategy and Math Utilities."""

import pytest
import numpy as np
import pandas as pd
from sector_quant.strategies.math_utils import (
    rolling_ols,
    calculate_spread_and_zscore,
    estimate_cointegration_half_life,
)
from sector_quant.strategies.pairs_trading import (
    SectorPairsTradingStrategy,
    PairConfig,
    PairPositionState,
)
from sector_quant.events.queue import EventQueue
from sector_quant.events.events import SignalType
from sector_quant.data.historic_sector import HistoricSectorDataHandler


def test_rolling_ols_perfect_linear():
    x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    y = 5.0 + 1.5 * x  # alpha=5, beta=1.5
    beta, alpha, r2 = rolling_ols(y, x)
    assert pytest.approx(beta, rel=1e-4) == 1.5
    assert pytest.approx(alpha, rel=1e-4) == 5.0
    assert pytest.approx(r2, rel=1e-4) == 1.0


def test_calculate_spread_and_zscore():
    x = np.linspace(100, 110, 50)
    y = 1.2 * x + np.random.normal(0, 0.5, 50)
    # inject large spike at end
    y[-1] += 5.0

    res = calculate_spread_and_zscore(y, x, z_lookback=30)
    assert res["beta"] > 0
    assert res["z_score"] > 2.0  # Spike should trigger positive z-score


def test_half_life_estimation():
    # Generate OU process
    spread = [0.0]
    for _ in range(200):
        spread.append(spread[-1] + 0.2 * (0 - spread[-1]) + np.random.normal(0, 1))
    hl = estimate_cointegration_half_life(np.array(spread))
    assert 1.0 < hl < 20.0


def test_pairs_strategy_signal_generation():
    queue = EventQueue()
    dates = pd.date_range("2024-01-01", periods=60, freq="B")

    # Generate baseline series
    x_prices = 100.0 + np.sin(np.linspace(0, 10, 60)) * 5.0
    y_prices = 1.5 * x_prices.copy()
    # At bar 55, create spread expansion (divergence)
    y_prices[55] += 15.0

    df_x = pd.DataFrame({"date": dates, "open": x_prices, "high": x_prices, "low": x_prices, "close": x_prices, "volume": 1000})
    df_y = pd.DataFrame({"date": dates, "open": y_prices, "high": y_prices, "low": y_prices, "close": y_prices, "volume": 1000})

    handler = HistoricSectorDataHandler(
        events_queue=queue,
        symbol_list=["STOCK_Y", "STOCK_X"],
        data_dict={"STOCK_Y": df_y, "STOCK_X": df_x},
    )

    pair_cfg = PairConfig(
        symbol_y="STOCK_Y",
        symbol_x="STOCK_X",
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

    # Process all bars through strategy
    signals_emitted = []
    while handler.continue_backtest:
        if handler.update_bars():
            market_event = queue.get()
            strategy.calculate_signals(market_event)
            while not queue.empty():
                sig = queue.get()
                signals_emitted.append(sig)

    assert len(signals_emitted) > 0
    # Should contain SHORT for Y and LONG for X on positive spread spike
    short_sigs = [s for s in signals_emitted if s.symbol == "STOCK_Y" and s.signal_type == SignalType.SHORT]
    assert len(short_sigs) > 0

