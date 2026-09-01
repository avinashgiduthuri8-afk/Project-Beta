"""Integration tests for BacktestEngine, Walk-Forward Validation, and Sensitivity Analysis."""

import pytest
import pandas as pd
import numpy as np
from sector_quant.db.master import SecuritiesMaster
from sector_quant.db.ingestion import SectorDataIngestor
from sector_quant.events.queue import EventQueue
from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.strategies.pairs_trading import SectorPairsTradingStrategy, PairConfig
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.execution.simulated import SimulatedExecutionHandler, ExecutionCostConfig
from sector_quant.backtest.engine import BacktestEngine
from sector_quant.backtest.walk_forward import WalkForwardValidator
from sector_quant.backtest.sensitivity import SensitivityAnalyzer


@pytest.fixture
def synthetic_sector_data():
    master = SecuritiesMaster(":memory:")
    ingestor = SectorDataIngestor(master)
    data = ingestor.generate_synthetic_sector_data(
        sector_code="ENERGY",
        benchmark_ticker="XLE",
        constituent_tickers=["XOM", "CVX"],
        num_days=120,
        seed=42,
        cointegrated_pairs=[("XOM", "CVX", 1.25)],
    )
    return data


def test_end_to_end_backtest_loop(synthetic_sector_data):
    queue = EventQueue()
    data_handler = HistoricSectorDataHandler(
        events_queue=queue,
        data_dict=synthetic_sector_data,
    )
    strategy = SectorPairsTradingStrategy(
        bars=data_handler,
        events_queue=queue,
        pairs=[PairConfig(symbol_y="XOM", symbol_x="CVX", ols_window=30, z_lookback=20, z_entry=1.8, z_exit=0.4)],
    )
    portfolio = SectorPortfolio(
        bars=data_handler,
        events_queue=queue,
        initial_capital=100_000.0,
    )
    execution_handler = SimulatedExecutionHandler(
        events_queue=queue,
        bars=data_handler,
        cost_config=ExecutionCostConfig(),
    )
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=execution_handler,
        events_queue=queue,
    )

    results = engine.run_backtest()
    assert results["iterations"] > 0
    assert "sharpe_ratio" in results
    assert "max_drawdown" in results
    assert len(engine.get_equity_curve()) > 0


def test_walk_forward_validator(synthetic_sector_data):
    validator = WalkForwardValidator(
        data_dict=synthetic_sector_data,
        strategy_factory=lambda dh, q: SectorPairsTradingStrategy(
            bars=dh, events_queue=q, pairs=[PairConfig(symbol_y="XOM", symbol_x="CVX", ols_window=30, z_lookback=20)]
        ),
        initial_capital=100_000.0,
        train_ratio=0.70,
    )
    wf_res = validator.run_train_test_split()
    assert "in_sample" in wf_res
    assert "out_of_sample" in wf_res
    assert "walk_forward_efficiency" in wf_res


def test_sensitivity_analyzer(synthetic_sector_data):
    analyzer = SensitivityAnalyzer(
        data_dict=synthetic_sector_data,
        initial_capital=100_000.0,
    )
    cost_df = analyzer.evaluate_cost_drag(
        strategy_factory=lambda dh, q: SectorPairsTradingStrategy(
            bars=dh, events_queue=q, pairs=[PairConfig(symbol_y="XOM", symbol_x="CVX", ols_window=30, z_lookback=20)]
        ),
        slippage_levels_bps=[0.0, 5.0],
        per_share_commissions=[0.0, 0.005],
    )
    assert not cost_df.empty
    assert len(cost_df) == 4
    assert "sharpe_ratio" in cost_df.columns

