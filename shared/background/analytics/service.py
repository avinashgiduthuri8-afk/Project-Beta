"""
V2 Quantitative Analytics Service.

Unified service facade coordinating AnalyticsEngine and TaxLedgerService.
"""

from __future__ import annotations

from typing import Any

from core.logging import get_logger
from core.repository.journal_repo import JournalRepository

from .engine import AnalyticsEngine
from .tax_ledger import TaxLedgerService

logger = get_logger("background.analytics")


class AnalyticsService:
    """Quantitative Analytics & Tax Ledger Service."""

    def __init__(self, journal_repo: JournalRepository) -> None:
        self._journal_repo = journal_repo
        self.engine = AnalyticsEngine(journal_repo=journal_repo)
        self.tax_ledger = TaxLedgerService(journal_repo=journal_repo)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        logger.info("AnalyticsService started with AnalyticsEngine & TaxLedgerService")

    async def stop(self) -> None:
        self._started = False
        logger.info("AnalyticsService stopped")

    async def get_performance_summary(
        self,
        bot_name: str | None = None,
        pair: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Fetch quantitative performance metrics summary."""
        return await self.engine.compute_performance_metrics(
            bot_name=bot_name, pair=pair, limit=limit
        )

    async def get_tax_ledger_summary(
        self,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> dict[str, Any]:
        """Fetch statutory tax & compliance summary."""
        return await self.tax_ledger.get_tax_summary(
            start_iso=start_iso, end_iso=end_iso
        )
