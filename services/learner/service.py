"""
PROJECT-ALPHA — Autonomous Learner Service (Stages 8–10).

Coordinating the Autonomous Learning, Walk-Forward Validation, and Canary Calibration Loop:
  1. Stage 8: Extract mistake patterns from closed paper trade journals.
  2. Stage 8/9: Dynamically compute strategy calibrations under strict immutable safety gates.
  3. Stage 9: Execute pre-deployment historical replay backtest validation to prevent degradation.
  4. Stage 10: Promote validated parameters to active cache, monitor post-promotion trade outcomes,
     and trigger automated rollback on consecutive losses.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from background.analytics.engine import AnalyticsEngine
from background.backtest.service.service import BacktestService
from background.feedback.orchestrator import FeedbackOrchestrator
from background.learning.calibrator import StrategyCalibrator
from background.learning.engine import LearningEngine
from core.bus.event_bus import EventBus
from core.logging import get_logger
from core.repository.backtest_repo import BacktestRepository
from core.repository.candle_repo import CandleRepository
from core.repository.feedback_repo import FeedbackRepository
from core.repository.journal_repo import JournalRepository
from core.repository.learning_repo import LearningRepository

from .config import LearnerConfig

logger = get_logger("learner.service")


class LearnerService:
    """Unified Autonomous Learner & Canary Calibration Service."""

    def __init__(
        self,
        config: LearnerConfig,
        journal_repo: JournalRepository,
        learning_repo: LearningRepository,
        feedback_repo: FeedbackRepository,
        backtest_repo: BacktestRepository,
        candle_repo: CandleRepository | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._journal_repo = journal_repo
        self._learning_repo = learning_repo
        self._feedback_repo = feedback_repo
        self._backtest_repo = backtest_repo
        self._candle_repo = candle_repo
        self._bus = bus or EventBus()

        self._analytics_engine = AnalyticsEngine(self._journal_repo)
        self.learning_engine = LearningEngine(
            journal_repo=self._journal_repo,
            learning_repo=self._learning_repo,
        )
        self.calibrator = StrategyCalibrator(
            learning_repo=self._learning_repo,
            analytics_engine=self._analytics_engine,
            bus=self._bus,
        )
        self.backtest_service = BacktestService(
            backtest_repo=self._backtest_repo,
        )
        self.feedback_orchestrator = FeedbackOrchestrator(
            feedback_repo=self._feedback_repo,
            backtest_service=self.backtest_service,
            bus=self._bus,
        )

        self._running = False
        self._task: asyncio.Task | None = None
        self._last_report: dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_report(self) -> dict[str, Any]:
        return dict(self._last_report)

    async def start(self) -> None:
        """Start the continuous background learning loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "LearnerService continuous loop started (interval=%ds)",
            self._config.poll_interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the background learning loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("LearnerService stopped")

    async def _loop(self) -> None:
        # Run an initial cycle on startup
        try:
            await self.run_cycle()
        except Exception:
            logger.exception("Initial learning cycle encountered error")

        while self._running:
            try:
                await asyncio.sleep(self._config.poll_interval_seconds)
                if not self._running:
                    break
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in LearnerService loop")

    async def run_cycle(self, limit: int = 100) -> dict[str, Any]:
        """
        Execute an end-to-end autonomous learning pass:
          1. Ingest recent paper trade journals.
          2. Extract mistake patterns & insights (Stage 8).
          3. Calculate candidate strategy calibrations (Stage 8/9).
          4. Validate candidates via backtest simulations (Stage 9).
          5. Promote or reject calibrations and verify rollback invariants (Stage 10).
        """
        now_str = datetime.now(timezone.utc).isoformat()
        logger.info("Beginning autonomous learning cycle pass...")

        entries = await self._journal_repo.get_entries(limit=limit, offset=0)
        entry_count = len(entries)

        if entry_count < self._config.min_trades_for_analysis:
            logger.info(
                "Insufficient trades for learning analysis (%d / %d required). Skipping cycle.",
                entry_count,
                self._config.min_trades_for_analysis,
            )
            report = {
                "status": "INSUFFICIENT_DATA",
                "trade_count": entry_count,
                "insights_count": 0,
                "calibrations_count": 0,
                "promoted_count": 0,
                "rejected_count": 0,
                "unchanged_count": 0,
                "rolled_back_count": 0,
                "timestamp": now_str,
            }
            self._last_report = report
            return report

        # Process each closed trade once against an already-active canary before
        # considering a new promotion. Historical pre-promotion trades are ignored.
        rolled_back_count = 0
        rolled_back_bots: set[str] = set()
        validation_audit_events: list[dict[str, Any]] = []
        sorted_recent = sorted(
            entries, key=lambda x: str(x.get("exit_timestamp", ""))
        )
        for trade in sorted_recent:
            bot = str(trade.get("bot_name", "")).upper()
            if not bot:
                continue
            rb_event = await self.feedback_orchestrator.register_trade_outcome(
                bot_name=bot,
                pair=str(trade.get("pair", "BTC/INR")),
                is_win=float(trade.get("net_pnl", 0.0)) > 0,
                trade_id=str(trade.get("id") or trade.get("position_id") or ""),
                exited_at=str(trade.get("exit_timestamp") or ""),
            )
            if rb_event is not None:
                rolled_back_count += 1
                rolled_back_bots.add(bot)
                validation_audit_events.append(rb_event)

        # 1. Mistake Identification (Stage 8)
        insights = await self.learning_engine.analyze_trades_and_extract_insights(limit=limit)

        # 2. Dynamic Strategy Calibration (Stage 8/9)
        calibrations = await self.calibrator.calibrate_all_strategies(insights)

        promoted_count = 0
        rejected_count = 0
        unchanged_count = 0
        # 3. Pre-Deployment Validation Gate & Canary Promotion (Stage 9 & 10)
        for cal in calibrations:
            bot_name = str(cal.get("bot_name", "STE")).upper()
            pair = str(cal.get("pair") or "BTC/INR")
            mult = float(cal.get("weight_multiplier", 1.0))
            thresh = float(cal.get("min_confluence_threshold", 85.0))

            if bot_name in rolled_back_bots:
                logger.warning(
                    "Skipping same-cycle re-promotion for %s after safety rollback",
                    bot_name,
                )
                rejected_count += 1
                continue

            validation_candles: list[dict[str, Any]] = []
            if self._config.enable_prevalidation_backtest and self._candle_repo:
                try:
                    validation_candles = await self._candle_repo.get_recent_candles(
                        pair=pair, timeframe="1h", limit=500
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not load validation candles for %s: %s", pair, exc
                    )

            audit = await self.feedback_orchestrator.evaluate_and_validate_calibration(
                bot_name=bot_name,
                pair=pair,
                proposed_multiplier=mult,
                proposed_threshold=thresh,
                validation_candles=validation_candles,
            )
            validation_audit_events.append(audit)
            if audit.get("status") == "PROMOTED":
                promoted_count += 1
            elif audit.get("status") == "UNCHANGED":
                unchanged_count += 1
            else:
                rejected_count += 1

        report = {
            "status": "COMPLETED",
            "trade_count": entry_count,
            "insights_count": len(insights),
            "calibrations_count": len(calibrations),
            "promoted_count": promoted_count,
            "rejected_count": rejected_count,
            "unchanged_count": unchanged_count,
            "rolled_back_count": rolled_back_count,
            "validation_events": validation_audit_events,
            "timestamp": now_str,
        }
        self._last_report = report
        logger.info(
            "Completed learning cycle: %d trades, %d insights, %d calibrations, %d promoted, %d unchanged, %d rejected, %d rollbacks",
            entry_count,
            len(insights),
            len(calibrations),
            promoted_count,
            unchanged_count,
            rejected_count,
            rolled_back_count,
        )
        return report

    async def get_active_calibrations(self) -> dict[str, dict[str, Any]]:
        """Return active strategy calibrations cache."""
        return await self._feedback_repo.get_all_active_calibrations()

    async def get_audit_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return chronological feedback and canary audit records."""
        return await self._feedback_repo.get_audit_history(limit=limit)
