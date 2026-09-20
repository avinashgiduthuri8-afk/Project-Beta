"""V2 Bot Execution Engine — Event-driven runner."""

import asyncio
import logging
from typing import Dict, Any

from v2.core.pipeline_orchestrator import ExecutionPipelineOrchestrator
from v2.core.bots.registry import BotRegistry
from v2.services.portfolio_service.service import PortfolioService
from v2.market_data.ticker_stream import TickerStream
from v2.bus.event_bus import bus
from v2.bus.event_types import EventType
from v2.execution.scheduler import TaskScheduler
import pandas as pd

logger = logging.getLogger("v2.execution.bot_runner")

class BotExecutionEngine:
    """Listens to market ticks, runs evaluation pipelines, updates portfolios."""

    def __init__(
        self,
        orchestrator: ExecutionPipelineOrchestrator,
        registry: BotRegistry,
        portfolio_service: PortfolioService,
        ticker_stream: TickerStream,
    ):
        self.orchestrator = orchestrator
        self.registry = registry
        self.portfolio_service = portfolio_service
        self.ticker_stream = ticker_stream
        self.scheduler = TaskScheduler()
        self._running = False

    async def initialize(self) -> None:
        """Sets up event subscriptions and initializes components."""
        bus.subscribe(EventType.MARKET_TICK, self._on_market_tick)
        await self.portfolio_service.initialize()
        await self.ticker_stream.initialize_cache()

    async def start(self) -> None:
        """Starts the execution engine."""
        self._running = True
        logger.info("BotExecutionEngine started.")
        await self.ticker_stream.start()
        
        # Start Scheduler and register jobs
        await self.scheduler.start()
        
        # Job 1: M2M Exit Monitor (Check trailing stops / exit conditions every 5s)
        # Note: update_prices handles M2M tracking already, but a dedicated check is good
        self.scheduler.schedule("m2m_exit_monitor", 5.0, self._job_m2m_exit_monitor)
        
        # Job 2: Portfolio Snapshot (Every 10 minutes = 600s)
        self.scheduler.schedule("portfolio_snapshot", 600.0, self._job_portfolio_snapshot)
        
        # Job 3: Order Reconciliation (Every 60s)
        self.scheduler.schedule("order_reconciliation", 60.0, self._job_order_reconciliation)

    async def stop(self) -> None:
        """Stops the execution engine."""
        self._running = False
        await self.scheduler.stop()
        await self.ticker_stream.stop()
        logger.info("BotExecutionEngine stopped.")

    async def _job_m2m_exit_monitor(self) -> None:
        """Evaluates active positions for SL/TP or trailing stop triggers."""
        pass # To be implemented (usually via PositionManager evaluating exits)

    async def _job_portfolio_snapshot(self) -> None:
        """Records a periodic portfolio snapshot."""
        await self.portfolio_service.record_snapshot()
        
    async def _job_order_reconciliation(self) -> None:
        """Audits open positions against active broker orders."""
        pass # To be implemented

    async def _on_market_tick(self, payload: Dict[str, Any]) -> None:
        """Handler for MARKET_TICK events."""
        if not self._running:
            return

        prices = payload.get("prices", {})
        
        # 1. Mark-to-Market Portfolio Update
        await self.portfolio_service.update_prices(prices)

        # 2. Evaluate Setups across archetypes
        for symbol, ltp in prices.items():
            # In a real system, we'd fetch the latest DataFrame for the symbol here.
            # We mock a small DataFrame for the pipeline.
            df = pd.DataFrame({
                "timestamp": [pd.Timestamp.now()] * 50,
                "open": [ltp] * 50,
                "high": [ltp * 1.01] * 50,
                "low": [ltp * 0.99] * 50,
                "close": [ltp] * 50,
                "volume": [100000] * 50,
            })
            
            for bot in self.registry.get_all_bots():
                # Pipeline STAGE 1-14 Execution
                # Note: This is an async call in real implementation, but orchestrator 
                # might take the DataFrame and run the full pipeline.
                try:
                    await self.orchestrator.execute_pipeline_cycle(symbol, df)
                except Exception as e:
                    logger.error(f"Pipeline error for {bot.name.value} on {symbol}: {e}")
