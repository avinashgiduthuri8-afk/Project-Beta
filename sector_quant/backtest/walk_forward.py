"""Walk-Forward Optimization and In-Sample / Out-of-Sample Validator."""

from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Callable
import pandas as pd

from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.events.queue import EventQueue
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.execution.simulated import SimulatedExecutionHandler, ExecutionCostConfig
from sector_quant.backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


class WalkForwardValidator:
    """Performs In-Sample (IS) calibration and Out-of-Sample (OOS) validation."""

    def __init__(
        self,
        data_dict: Dict[str, pd.DataFrame],
        strategy_factory: Callable[[HistoricSectorDataHandler, EventQueue], Any],
        initial_capital: float = 100_000.0,
        cost_config: Optional[ExecutionCostConfig] = None,
        train_ratio: float = 0.70,
    ):
        self.data_dict = data_dict
        self.strategy_factory = strategy_factory
        self.initial_capital = initial_capital
        self.cost_config = cost_config or ExecutionCostConfig()
        self.train_ratio = train_ratio

    def run_train_test_split(self) -> Dict[str, Any]:
        """Splits multi-asset data into Train (In-Sample) and Test (Out-of-Sample) and runs backtests."""
        # Find global dates
        first_symbol = list(self.data_dict.keys())[0]
        df_sample = self.data_dict[first_symbol]
        dates = pd.to_datetime(df_sample["date"] if "date" in df_sample.columns else df_sample.index)

        split_idx = int(len(dates) * self.train_ratio)
        split_date = dates.iloc[split_idx]

        logger.info(f"Splitting dataset at date: {split_date} (Train: {split_idx} bars, Test: {len(dates) - split_idx} bars)")

        train_data = {}
        test_data = {}
        for sym, df in self.data_dict.items():
            d = df.copy()
            if "date" in d.columns:
                d["date"] = pd.to_datetime(d["date"])
                train_data[sym] = d[d["date"] < split_date].copy()
                test_data[sym] = d[d["date"] >= split_date].copy()
            else:
                train_data[sym] = d.iloc[:split_idx].copy()
                test_data[sym] = d.iloc[split_idx:].copy()

        # Run In-Sample Backtest
        is_results = self._run_single_split(train_data, name="In-Sample (IS)")

        # Run Out-of-Sample Backtest
        oos_results = self._run_single_split(test_data, name="Out-of-Sample (OOS)")

        # Calculate Walk Forward Efficiency (WFE) = OOS Annualized Return / IS Annualized Return
        is_cagr = is_results.get("cagr", 0.0)
        oos_cagr = oos_results.get("cagr", 0.0)
        wfe = (oos_cagr / is_cagr) if is_cagr > 0 else 0.0

        return {
            "split_date": str(split_date)[:10],
            "train_ratio": self.train_ratio,
            "in_sample": is_results,
            "out_of_sample": oos_results,
            "walk_forward_efficiency": float(wfe),
        }

    def _run_single_split(self, data_subset: Dict[str, pd.DataFrame], name: str) -> Dict[str, Any]:
        queue = EventQueue()
        data_handler = HistoricSectorDataHandler(
            events_queue=queue,
            data_dict=data_subset,
        )
        strategy = self.strategy_factory(data_handler, queue)
        portfolio = SectorPortfolio(
            bars=data_handler,
            events_queue=queue,
            initial_capital=self.initial_capital,
        )
        execution_handler = SimulatedExecutionHandler(
            events_queue=queue,
            bars=data_handler,
            cost_config=self.cost_config,
        )
        engine = BacktestEngine(
            data_handler=data_handler,
            strategy=strategy,
            portfolio=portfolio,
            execution_handler=execution_handler,
            events_queue=queue,
        )

        logger.info(f"Running {name} backtest...")
        return engine.run_backtest()

