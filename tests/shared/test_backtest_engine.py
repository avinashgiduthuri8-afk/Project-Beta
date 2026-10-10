"""
Unit & Integration Tests for PROJECT-ALPHA Backtesting Suite & CoinDCX Tax Friction Model.
"""

from __future__ import annotations

import pytest

from background.backtest.data_feeder import (
    COINDCX_INR_PAIRS,
    round_price,
    round_qty,
)
from background.backtest.engine import BacktestEngine
from background.backtest.fleet_selector import FleetSelector
from background.backtest.friction import CoinDCXFrictionModel, FrictionConfig
from background.backtest.metrics import PerformanceMetrics, calculate_trade_metrics
from background.backtest.risk_gate import Stage06RiskGate
from background.backtest.strategies import HDAStrategy


class TestFrictionModel:
    def test_inr_spot_friction_rates(self):
        config = FrictionConfig(is_c2c_pair=False)
        assert config.buy_fee_pct == 0.236  # 0.20% + 18% GST
        assert config.sell_fee_pct == 1.236  # 1.00% Sec 194S TDS + 0.236% fee
        assert config.round_trip_fee_pct == 1.472
        assert config.total_round_trip_drag_pct == 1.572  # 1.472% fee + 0.10% slippage

    def test_c2c_pair_friction_rates(self):
        config = FrictionConfig(is_c2c_pair=True)
        assert config.buy_fee_pct == 1.236  # 1% Buy TDS + 0.236% fee
        assert config.sell_fee_pct == 1.236  # 1% Sell TDS + 0.236% fee
        assert config.round_trip_fee_pct == 2.472
        assert config.total_round_trip_drag_pct == 2.572  # 2.472% fee + 0.10% slippage

    def test_trade_net_pnl_calculation(self):
        model = CoinDCXFrictionModel()
        pnl = model.calculate_trade_net_pnl(
            entry_price=100.0, exit_price=110.0, position_size_qty=10.0
        )
        assert pnl["gross_pnl"] == 100.0
        assert pnl["gross_pnl_pct"] == 10.0
        assert pnl["net_pnl"] < pnl["gross_pnl"]  # Friction deducted
        assert pnl["total_friction_cost"] > 0.0


class TestPrecisionRounding:
    def test_inr_pairs_count(self):
        assert len(COINDCX_INR_PAIRS) >= 10
        assert "BTC/INR" in COINDCX_INR_PAIRS
        assert "SOL/INR" in COINDCX_INR_PAIRS
        assert "DOGE/INR" in COINDCX_INR_PAIRS
        assert "SHIB/INR" in COINDCX_INR_PAIRS

    def test_price_rounding(self):
        assert round_price("BTC/INR", 8234567.8912) == 8234567.89
        assert round_price("SOL/INR", 12500.46) == 12500.5
        assert round_price("SHIB/INR", 0.001825678) == 0.001826

    def test_qty_rounding(self):
        assert round_qty("BTC/INR", 0.01234567) == 0.01234
        assert round_qty("SOL/INR", 1.458) == 1.45
        assert round_qty("DOGE/INR", 125.87) == 125.0
        assert round_qty("SHIB/INR", 56789.0) == 56000.0


class TestStage06RiskGate:
    def test_position_sizing_calculation(self):
        risk_gate = Stage06RiskGate(max_risk_pct_per_trade=1.0)
        sizing = risk_gate.calculate_position_size(
            account_equity=100000.0,
            entry_price=100.0,
            stop_loss_price=95.0,  # 5% SL
            pair="MATIC/INR",
        )
        assert sizing["risk_capital"] == 1000.0  # 1% of 100000
        assert sizing["qty"] > 0
        assert sizing["amount"] > 0
        assert sizing["stop_loss_pct"] == 5.0


class TestMetricsCalculator:
    def test_calculate_trade_metrics(self):
        trades = [
            {"net_pnl": 1500.0, "gross_pnl": 2000.0},
            {"net_pnl": 2000.0, "gross_pnl": 2500.0},
            {"net_pnl": -500.0, "gross_pnl": -300.0},
        ]
        m = calculate_trade_metrics("TestStrategy", trades, initial_capital=100000.0)
        assert m.total_trades == 3
        assert m.winning_trades == 2
        assert m.losing_trades == 1
        assert m.win_rate_pct == pytest.approx(66.67, abs=0.1)
        assert m.net_profit_factor == pytest.approx(7.0, abs=0.1)
        assert m.survives_friction is True


class TestFleetSelector:
    def test_evaluate_and_rank_fleet(self):
        selector = FleetSelector(
            min_net_pf=1.75, min_net_rr=1.50, max_drawdown_pct=15.0
        )
        m1 = PerformanceMetrics(
            "HDA", 50, 40, 10, 80.0, 10.0, 5.0, 50.0, 5000.0, 2.0, 3.0, 100.0, True
        )
        m2 = PerformanceMetrics(
            "STE", 50, 38, 12, 76.0, 8.0, 4.0, 40.0, 4000.0, 1.8, 4.0, 80.0, True
        )
        m3 = PerformanceMetrics(
            "WeakStrategy",
            50,
            10,
            40,
            20.0,
            0.8,
            0.5,
            -20.0,
            -2000.0,
            0.5,
            30.0,
            -40.0,
            False,
        )

        ranked, top_4 = selector.evaluate_and_rank_fleet([m1, m2, m3])
        assert len(ranked) == 3
        assert ranked[0].metrics.strategy_name == "HDA"
        assert ranked[0].passes_gate is True
        assert ranked[2].passes_gate is False
        assert len(top_4) <= 4


class TestBacktestEngineIntegration:
    def test_run_single_strategy_backtest(self):
        engine = BacktestEngine(initial_capital=100000.0)
        strat = HDAStrategy()
        m = engine.run_strategy_backtest(
            strat, pairs=["BTC/INR"], timeframes=["1H"], sessions=50
        )
        assert isinstance(m, PerformanceMetrics)
        assert m.strategy_name == strat.name
