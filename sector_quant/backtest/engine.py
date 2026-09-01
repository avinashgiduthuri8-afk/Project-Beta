"""Sequential Event-Driven Backtesting Engine with Zero Lookahead Bias."""

from __future__ import annotations

import logging
import time
from typing import Dict, Any, Optional
import pandas as pd

from sector_quant.data.base import DataHandler
from sector_quant.strategies.base import Strategy
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.execution.base import ExecutionHandler
from sector_quant.events.events import EventType, MarketEvent, SignalEvent, OrderEvent, FillEvent
from sector_quant.events.queue import EventQueue

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Orchestrates sequential event routing for historical quantitative backtesting."""

    def __init__(
        self,
        data_handler: DataHandler,
        strategy: Strategy,
        portfolio: SectorPortfolio,
        execution_handler: ExecutionHandler,
        events_queue: EventQueue,
    ):
        self.data_handler = data_handler
        self.strategy = strategy
        self.portfolio = portfolio
        self.execution_handler = execution_handler
        self.events_queue = events_queue

        self.iterations = 0
        self.execution_time_seconds = 0.0

    def run_backtest(self) -> Dict[str, Any]:
        """Executes the event-driven backtest loop until data stream is exhausted."""
        logger.info("Starting quantitative sector backtest orchestration loop...")
        start_time = time.time()
        self.iterations = 0

        while self.data_handler.continue_backtest:
            # 1. Drip-feed next synchronized bar across all symbols in the sector
            if not self.data_handler.update_bars():
                break

            self.iterations += 1

            # 2. Process all events generated for the current bar in strict sequential order
            while True:
                if self.events_queue.empty():
                    break

                try:
                    event = self.events_queue.get(block=False)
                except Exception:
                    break

                if event is not None:
                    if event.type == EventType.MARKET:
                        # Mark-to-market portfolio accounting first, then strategy signal generation
                        self.portfolio.update_timeindex(event)
                        self.strategy.calculate_signals(event)

                    elif event.type == EventType.SIGNAL:
                        # Portfolio converts signal to risk-checked OrderEvent
                        self.portfolio.update_signal(event)

                    elif event.type == EventType.ORDER:
                        # Execution handler fills OrderEvent into FillEvent
                        self.execution_handler.execute_order(event)

                    elif event.type == EventType.FILL:
                        # Portfolio marks execution, updates cash & positions
                        self.portfolio.update_fill(event)

        self.execution_time_seconds = time.time() - start_time
        logger.info(
            f"Backtest completed in {self.execution_time_seconds:.2f}s "
            f"({self.iterations} bars processed, {len(self.portfolio.trade_history)} fills)"
        )

        return self.get_results()

    def get_results(self) -> Dict[str, Any]:
        """Compiles backtest results, performance metrics, and equity curve."""
        summary = self.portfolio.get_performance_summary()
        summary["iterations"] = self.iterations
        summary["execution_time_seconds"] = self.execution_time_seconds
        summary["total_fills"] = len(self.portfolio.trade_history)
        summary["closed_trades_count"] = len(self.portfolio.closed_trades)
        return summary

    def get_equity_curve(self) -> pd.DataFrame:
        """Returns the final equity curve DataFrame."""
        return self.portfolio.get_equity_dataframe()

    def get_trade_log(self) -> list[Dict[str, Any]]:
        """Returns all executed trades."""
        return list(self.portfolio.trade_history)

