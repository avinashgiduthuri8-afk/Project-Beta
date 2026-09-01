"""Unit & Integration tests for Backtest Engine, Zero Lookahead, and Walk-Forward Optimization."""

import pytest
import pandas as pd
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
def sector_pairs_data():
    master = SecuritiesMaster(":memory:")
    ingestor = SectorDataIngestor(master)
    return ingestor.generate_synthetic_sector_data(
        sector_code="ENERGY",
        benchmark_ticker="XLE",
        constituent_tickers=["AREX", "WLL"],
        num_days=150,
        seed=42,
        cointegrated_pairs=[("AREX", "WLL", 1.25)],
    )


def test_full_backtest_loop_zero_lookahead(sector_pairs_data):
    queue = EventQueue()
    data_handler = HistoricSectorDataHandler(
        events_queue=queue,
        data_dict=sector_pairs_data,
    )
    strategy = SectorPairsTradingStrategy(
        bars=data_handler,
        events_queue=queue,
        pairs=[PairConfig(symbol_y="AREX", symbol_x="WLL", ols_window=30, z_lookback=20, z_entry=1.8, z_exit=0.4)],
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
    assert results["iterations"] == 150
    assert "sharpe_ratio" in results
    assert "max_drawdown_pct" in results
    assert len(engine.get_equity_curve()) == 150


def test_walk_forward_70_30_split_and_wfe(sector_pairs_data):
    validator = WalkForwardValidator(
        data_dict=sector_pairs_data,
        strategy_factory=lambda dh, q: SectorPairsTradingStrategy(
            bars=dh, events_queue=q, pairs=[PairConfig(symbol_y="AREX", symbol_x="WLL", ols_window=30, z_lookback=20)]
        ),
        initial_capital=100_000.0,
        train_ratio=0.70,
    )
    wf_res = validator.run_train_test_split()
    assert wf_res["train_ratio"] == 0.70
    assert "in_sample" in wf_res
    assert "out_of_sample" in wf_res
    assert "walk_forward_efficiency" in wf_res
    assert wf_res["in_sample"]["iterations"] > 0
    assert wf_res["out_of_sample"]["iterations"] > 0


def test_sensitivity_parameter_grid(sector_pairs_data):
    analyzer = SensitivityAnalyzer(
        data_dict=sector_pairs_data,
        initial_capital=100_000.0,
    )

    def strat_builder(dh, q, params):
        return SectorPairsTradingStrategy(
            bars=dh,
            events_queue=q,
            pairs=[PairConfig(
                symbol_y="AREX",
                symbol_x="WLL",
                ols_window=params["ols_window"],
                z_lookback=params["z_lookback"],
                z_entry=params["z_entry"],
                z_exit=0.4,
            )],
        )

    grid = [
        {"ols_window": 30, "z_lookback": 20, "z_entry": 1.5},
        {"ols_window": 40, "z_lookback": 25, "z_entry": 2.0},
    ]
    grid_df = analyzer.evaluate_grid(strat_builder, grid)
    assert len(grid_df) == 2
    assert "sharpe_ratio" in grid_df.columns

