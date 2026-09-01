"""Runnable demonstration: Sector Momentum & Relative Strength Backtest."""

from __future__ import annotations

import logging
from sector_quant.db.master import SecuritiesMaster
from sector_quant.db.ingestion import SectorDataIngestor
from sector_quant.events.queue import EventQueue
from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.strategies.sector_momentum import SectorMomentumStrategy, SectorUniverseConfig
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.portfolio.risk_engine import SectorRiskEngine, RiskLimits
from sector_quant.portfolio.position_sizer import PositionSizer
from sector_quant.execution.simulated import SimulatedExecutionHandler, ExecutionCostConfig, CommissionScheme
from sector_quant.backtest.engine import BacktestEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    print("=" * 75)
    print("  QUANTITATIVE SECTOR MOMENTUM & RELATIVE STRENGTH BACKTEST DEMO")
    print("=" * 75)

    # 1. Initialize Securities Master DB and Ingest Data
    master = SecuritiesMaster(":memory:")
    ingestor = SectorDataIngestor(master)

    sector_code = "TECH"
    bench_symbol = "XLK"
    constituents = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMD"]

    print(f"\n[1] Generating synchronized sector data for {sector_code} ({bench_symbol}, {constituents})...")
    sector_data = ingestor.generate_synthetic_sector_data(
        sector_code=sector_code,
        benchmark_ticker=bench_symbol,
        constituent_tickers=constituents,
        start_date="2023-01-01",
        num_days=300,
        seed=202,
    )

    # 2. Setup Event-Driven Architecture
    all_symbols = [bench_symbol] + constituents
    events_queue = EventQueue()
    data_handler = HistoricSectorDataHandler(
        events_queue=events_queue,
        symbol_list=all_symbols,
        data_dict=sector_data,
    )

    # 3. Configure Momentum Strategy
    sector_cfg = SectorUniverseConfig(
        sector_code=sector_code,
        benchmark_symbol=bench_symbol,
        constituents=constituents,
        lookback_window=20,
        top_n=2,
        min_relative_strength=0.01,
        rebalance_frequency=5,
    )
    strategy = SectorMomentumStrategy(
        bars=data_handler,
        events_queue=events_queue,
        sector_config=sector_cfg,
        strategy_id="TECH_MOMENTUM_01",
    )

    # 4. Setup Risk-Governed Portfolio
    risk_limits = RiskLimits(
        max_sector_allocation=0.40,
        max_stock_allocation=0.20,
        max_gross_leverage=1.2,
    )
    risk_engine = SectorRiskEngine(limits=risk_limits)
    position_sizer = PositionSizer(default_stock_weight=0.15)
    portfolio = SectorPortfolio(
        bars=data_handler,
        events_queue=events_queue,
        initial_capital=100_000.0,
        risk_engine=risk_engine,
        position_sizer=position_sizer,
        symbol_to_sector={s: sector_code for s in all_symbols},
    )

    # 5. Setup Execution Handler
    cost_config = ExecutionCostConfig(
        commission_scheme=CommissionScheme.PER_SHARE,
        per_share_rate=0.005,
        min_commission=1.0,
        slippage_bps=3.0,
    )
    execution_handler = SimulatedExecutionHandler(
        events_queue=events_queue,
        bars=data_handler,
        cost_config=cost_config,
    )

    # 6. Execute Backtest
    print("\n[2] Executing Event-Driven Backtest Loop...")
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        portfolio=portfolio,
        execution_handler=execution_handler,
        events_queue=events_queue,
    )
    results = engine.run_backtest()

    # 7. Print Performance Summary
    print("\n" + "=" * 75)
    print("  MOMENTUM STRATEGY PERFORMANCE RESULTS")
    print("=" * 75)
    print(f"  Initial Equity          : ${results['initial_equity']:,.2f}")
    print(f"  Final Equity            : ${results['final_equity']:,.2f}")
    print(f"  Total Return            : {results['total_return_pct']:.2f}%")
    print(f"  CAGR                    : {results['cagr_pct']:.2f}%")
    print(f"  Sharpe Ratio            : {results['sharpe_ratio']:.2f}")
    print(f"  Sortino Ratio           : {results['sortino_ratio']:.2f}")
    print(f"  Max Drawdown            : {results['max_drawdown_pct']:.2f}%")
    print(f"  Max Drawdown Duration   : {results['max_drawdown_duration_bars']} bars")
    print(f"  Calmar Ratio            : {results['calmar_ratio']:.2f}")
    print(f"  Total Closed Trades     : {results['total_trades']}")
    print(f"  Win Rate                : {results['win_rate_pct']:.2f}%")
    print(f"  Profit Factor           : {results['profit_factor']:.2f}")
    print(f"  Total Fills             : {results['total_fills']}")
    print(f"  Cumulative Commission   : ${results['cumulative_commission']:.2f}")
    print(f"  Execution Time          : {results['execution_time_seconds']:.3f}s")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    main()

