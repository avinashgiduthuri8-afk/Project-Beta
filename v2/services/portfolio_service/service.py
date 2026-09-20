"""
V2 Portfolio Service — Manages multi-bot portfolios, AUM, and M2M pricing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from v2.core.logging import get_logger
from v2.core.types import BotName, BotStatus, BotMode, BotSnapshot, PortfolioSnapshot
from v2.trading.position_manager import PositionManager
from v2.repository.metrics_repo import MetricsRepository, MetricsSnapshot
from v2.repository.base import BaseRepository  # for bot_snapshots if we add a direct method, or we can use db

logger = get_logger("v2.services.portfolio_service")


class PortfolioService:
    """
    Maintains live AUM, deployed capital, and cash balances across all bots.
    Handles mark-to-market pricing updates and periodic snapshots.
    """

    def __init__(
        self,
        position_manager: PositionManager,
        metrics_repo: Optional[MetricsRepository] = None,
        initial_cash: float = 1_000_000.0,
    ):
        self.position_manager = position_manager
        self.metrics_repo = metrics_repo
        
        # Base capital allocation
        self.initial_cash = initial_cash
        self.cash_balance = initial_cash
        
        # Dynamic metrics
        self.total_aum = initial_cash
        self.total_deployed = 0.0
        self.total_unrealised_pnl = 0.0
        self.total_realised_pnl = 0.0
        self.daily_pnl = 0.0
        
        # Active bots tracking (mock state for now)
        self.active_bots: Dict[BotName, BotSnapshot] = {}

    async def initialize(self) -> None:
        """Loads positions from repository and initializes portfolio state."""
        await self.position_manager.load_active_positions()
        await self.recalculate_portfolio()
        logger.info(f"PortfolioService initialized. AUM: {self.total_aum}, Cash: {self.cash_balance}")

    async def recalculate_portfolio(self) -> None:
        """Recalculates all portfolio metrics based on active positions and cash."""
        active_positions = self.position_manager.get_active_positions()
        
        self.total_deployed = 0.0
        self.total_unrealised_pnl = 0.0
        
        for pos in active_positions:
            self.total_deployed += pos.deployed_capital
            self.total_unrealised_pnl += (pos.unrealised_pnl or 0.0)

        # Basic AUM logic: AUM = Cash + Deployed + Unrealised
        # Realized PnL adjusts Cash over time via trade settlement (handled separately)
        self.total_aum = self.cash_balance + self.total_deployed + self.total_unrealised_pnl
        
    def get_available_cash(self) -> float:
        """Returns the current free cash balance."""
        return self.cash_balance

    def get_total_aum(self) -> float:
        """Returns total Assets Under Management."""
        return self.total_aum

    async def update_prices(self, price_map: Dict[str, float]) -> None:
        """
        Mark-to-Market Pricing (BETA-CODE-09):
        Receives latest prices (e.g., from Event Bus ticks), updates positions,
        and recalculates portfolio AUM.
        """
        # Let position manager update its models and DB
        await self.position_manager.update_market_prices(price_map)
        # Recalculate top-level portfolio aggregates
        await self.recalculate_portfolio()

    async def record_snapshot(self) -> PortfolioSnapshot:
        """
        Snapshots & Persistence (BETA-CODE-10):
        Captures current portfolio state and optionally persists to DB.
        """
        await self.recalculate_portfolio()
        
        capital_util = 0.0
        if self.total_aum > 0:
            capital_util = (self.total_deployed / self.total_aum) * 100.0

        positions_by_bot = {}
        for pos in self.position_manager.get_active_positions():
            positions_by_bot.setdefault(pos.bot, []).append(pos)

        now = datetime.now(timezone.utc)
        
        snapshot = PortfolioSnapshot(
            total_aum=self.total_aum,
            total_deployed=self.total_deployed,
            total_cash=self.cash_balance,
            total_unrealised_pnl=self.total_unrealised_pnl,
            total_realised_pnl=self.total_realised_pnl,
            daily_pnl=self.daily_pnl,
            capital_utilisation=capital_util,
            positions_by_bot=positions_by_bot,
            captured_at=now,
        )

        if self.metrics_repo:
            metrics_snap = MetricsSnapshot(
                id=str(uuid.uuid4()),
                captured_at=now,
                total_aum=self.total_aum,
                total_deployed=self.total_deployed,
                total_cash=self.cash_balance,
                total_unrealised=self.total_unrealised_pnl,
                total_realised=self.total_realised_pnl,
                daily_pnl=self.daily_pnl,
                capital_util_pct=capital_util,
                per_bot={bot.value: len(pos_list) for bot, pos_list in positions_by_bot.items()}
            )
            await self.metrics_repo.insert_snapshot(metrics_snap)
            
            # Also insert BotSnapshots if needed (we can write raw SQL or add to repo)
            for bot, pos_list in positions_by_bot.items():
                bot_pnl = sum(p.unrealised_pnl or 0.0 for p in pos_list)
                bot_deployed = sum(p.deployed_capital for p in pos_list)
                bot_snap = BotSnapshot(
                    bot=bot,
                    mode=pos_list[0].mode if pos_list else BotMode.LIVE,
                    status=BotStatus.RUNNING,
                    cash_balance=0.0, # Handled at portfolio level in this model
                    deployed_capital=bot_deployed,
                    open_positions=len(pos_list),
                    total_pnl=bot_pnl,
                    last_cycle_at=now,
                    health_score=100,
                    captured_at=now,
                )
                await self._persist_bot_snapshot(bot_snap)

        return snapshot

    async def _persist_bot_snapshot(self, snap: BotSnapshot) -> None:
        """Helper to persist BotSnapshot to the database using the metrics repo's connection."""
        if not self.metrics_repo:
            return
            
        sid = str(uuid.uuid4())
        await self.metrics_repo._execute(
            """
            INSERT INTO bot_snapshots 
            (id, bot, mode, status, cash_balance, deployed_capital, 
             open_positions, total_pnl, health_score, captured_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sid,
                snap.bot.value,
                snap.mode.value,
                snap.status.value,
                snap.cash_balance,
                snap.deployed_capital,
                snap.open_positions,
                snap.total_pnl,
                snap.health_score,
                snap.captured_at.isoformat()
            )
        )

