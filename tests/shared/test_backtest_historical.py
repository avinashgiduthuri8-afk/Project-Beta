"""
PROJECT-ALPHA — Phase 5 Test Suite:
  - Historical Candle Ingestion for Backtest Engine (SQLite and CSV datasets)
  - Zero Look-Ahead Bias Execution on Bar N+1 Open
  - 1.572% Statutory Friction Deductions on Historical Fills
  - Multi-Timeframe Strategy Simulations across Production Bots (STE, HDA, VCP, BBS)
"""

from __future__ import annotations

import csv
import sqlite3

import numpy as np
import pandas as pd
import pytest

from background.backtest.data_feeder import DataFeeder
from background.backtest.engine import BacktestEngine
from background.backtest.friction import CoinDCXFrictionModel
from background.backtest.strategies import (
    BacktestTradeSignal,
    BBSStrategy,
    HDAStrategy,
    STEStrategy,
    VCPStrategy,
)


def _generate_sample_candles(
    count: int = 150, base_price: float = 60000.0, step: float = 50.0
) -> list[dict]:
    candles = []
    base_ts = 1700000000000
    for i in range(count):
        # Create a trending price wave with breakouts
        price = base_price + np.sin(i / 10.0) * 1500.0 + (i * step)
        candles.append(
            {
                "pair": "BTC/INR",
                "timeframe": "1H",
                "timestamp": base_ts + (i * 3600000),
                "open": round(price - 25.0, 2),
                "high": round(price + 100.0, 2),
                "low": round(price - 100.0, 2),
                "close": round(price + 25.0, 2),
                "volume": round(15.0 + (i % 5) * 10.0, 2),
            }
        )
    return candles


# ── 1. DataFeeder Ingestion Tests ─────────────────────────────────────────────


def test_datafeeder_load_from_records():
    feeder = DataFeeder()
    records = _generate_sample_candles(count=50)
    df = feeder.load_candles_from_records(records, pair="BTC/INR", timeframe="1H")

    assert not df.empty
    assert len(df) == 50
    assert "ema_9" in df.columns
    assert "ema_21" in df.columns
    assert "rsi_14" in df.columns
    assert "atr_14" in df.columns
    assert "donchian_high_20" in df.columns
    assert "bb_upper" in df.columns
    assert "vwap" in df.columns


def test_datafeeder_load_from_csv(tmp_path):
    feeder = DataFeeder()
    csv_file = tmp_path / "btc_historical.csv"

    candles = _generate_sample_candles(count=60)
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"]
        )
        writer.writeheader()
        for c in candles:
            writer.writerow(
                {
                    "timestamp": c["timestamp"],
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                }
            )

    df = feeder.load_candles_from_csv(csv_file, pair="BTC/INR", timeframe="1H")
    assert not df.empty
    assert len(df) == 60
    assert "ema_50" in df.columns
    assert "macd" in df.columns
    # Check chronological ordering
    assert df["timestamp"].is_monotonic_increasing


def test_datafeeder_load_from_sqlite(tmp_path):
    feeder = DataFeeder()
    db_file = tmp_path / "test_market.db"

    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE market_candles (
            pair TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (pair, timeframe, timestamp)
        )
    """)

    candles = _generate_sample_candles(count=80)
    conn.executemany(
        """
        INSERT INTO market_candles (pair, timeframe, timestamp, open, high, low, close, volume)
        VALUES (:pair, :timeframe, :timestamp, :open, :high, :low, :close, :volume)
    """,
        candles,
    )
    conn.commit()
    conn.close()

    df = feeder.load_candles_from_db(pair="BTC/INR", timeframe="1H", db_path=db_file)
    assert not df.empty
    assert len(df) == 80
    assert "rvol" in df.columns
    assert "cvd" in df.columns


# ── 2. Zero Look-Ahead Bias & Trade Execution Simulation ──────────────────────


def test_zero_lookahead_bias_execution():
    engine = BacktestEngine(initial_capital=100000.0)
    feeder = DataFeeder()

    candles = _generate_sample_candles(count=50, base_price=50000.0)
    df = feeder.load_candles_from_records(candles, pair="BTC/INR", timeframe="1H")

    # Signal triggered on bar index 10
    signal_bar = 10
    sig = BacktestTradeSignal(
        strategy_name="TEST_STRAT",
        bar_index=signal_bar,
        pair="BTC/INR",
        timeframe="1H",
        direction="LONG",
        entry_price=df["close"].iloc[signal_bar],  # Signal generated at bar 10 Close
        stop_loss_price=48000.0,
        take_profit_price=55000.0,
    )

    trades = engine._simulate_trade_executions(
        df, [sig], pair="BTC/INR", timeframe="1H"
    )
    assert len(trades) == 1
    trade = trades[0]

    # Verify execution happened strictly at bar 11 Open price
    expected_exec_price = df["open"].iloc[signal_bar + 1]
    assert trade["entry_price"] == expected_exec_price
    assert pd.to_datetime(trade["trigger_time"], utc=True) == pd.to_datetime(
        df["timestamp"].iloc[signal_bar], utc=True
    )
    assert pd.to_datetime(trade["exec_time"], utc=True) == pd.to_datetime(
        df["timestamp"].iloc[signal_bar + 1], utc=True
    )


# ── 3. Friction & Statutory Fee Deduction Tests ───────────────────────────────


def test_statutory_friction_accuracy_on_historical_trades():
    friction_model = CoinDCXFrictionModel()

    # 1.472% statutory friction + 0.10% slippage = 1.572% total round-trip friction
    entry_price = 100000.0
    exit_price = 100000.0  # Flat trade
    qty = 0.5  # ₹50,000 notional

    res = friction_model.calculate_trade_net_pnl(entry_price, exit_price, qty)
    assert res["gross_pnl"] == 0.0
    # Expected friction cost approx 1.572% of notional
    expected_friction_pct = friction_model.config.total_round_trip_drag_pct
    assert pytest.approx(res["net_pnl_pct"], abs=0.05) == -expected_friction_pct
    assert res["net_pnl"] < 0.0


# ── 4. Multi-Timeframe Strategy Historical Backtests ──────────────────────────


def test_historical_backtest_production_bots(tmp_path):
    engine = BacktestEngine(initial_capital=50000.0)
    feeder = DataFeeder()

    # Create CSV dataset for multi-timeframe BTC/INR
    csv_15m = tmp_path / "btc_15m.csv"
    csv_15m = tmp_path / "btc_15m.csv"

    candles_15m = _generate_sample_candles(count=200, base_price=60000.0, step=15.0)
    candles_15m = _generate_sample_candles(count=200, base_price=60000.0, step=60.0)

    for path, data in [(csv_15m, candles_15m), (csv_15m, candles_15m)]:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"]
            )
            writer.writeheader()
            for c in data:
                writer.writerow(
                    {
                        "timestamp": c["timestamp"],
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c["volume"],
                    }
                )

    csv_map = {
        "BTC/INR_15M": str(csv_15m),
        "BTC/INR_1H": str(csv_15m),
    }

    # Test all 4 production bots
    strategies = [STEStrategy(), HDAStrategy(), VCPStrategy(), BBSStrategy()]

    for strat in strategies:
        metrics = engine.run_historical_backtest(
            strategy=strat,
            pairs=["BTC/INR"],
            timeframes=["15M", "1H"],
            csv_paths=csv_map,
        )
        assert metrics is not None
        assert metrics.strategy_name == strat.name
        assert hasattr(metrics, "net_pnl_pct")
        assert hasattr(metrics, "win_rate_pct")
        assert hasattr(metrics, "max_drawdown_pct")
        assert hasattr(metrics, "net_profit_factor")
        assert hasattr(metrics, "expectancy_per_trade")
