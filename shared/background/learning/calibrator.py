"""
V2 Dynamic Parameter Calibrator.

Calculates strategy weight multipliers and confluence score thresholds dynamically
based on mistake pattern insights and quantitative performance metrics.
Publishes LEARNING_INSIGHT_GENERATED and STRATEGY_CALIBRATED events over EventBus.
"""

from __future__ import annotations

from typing import Any

from background.analytics.engine import AnalyticsEngine
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.learning_repo import LearningRepository

logger = get_logger("background.learning.calibrator")

# B10 Immutable Safety Boundary: Learning service CANNOT mutate risk gates or filter cascade rules
IMMUTABLE_SAFETY_BOUNDARIES: frozenset[str] = frozenset(
    {
        "scanner_min_24h_volume",
        "scanner_max_price_change_pct",
        "scanner_min_atr_pct",
        "scanner_max_atr_pct",
        "v2_scanner_max_signals",
        "enforce_single_coin_lock",
        "order_size_inr",
        "total_capital_limit",
        "v2_max_drawdown_pct",
    }
)


class StrategyCalibrator:
    """Dynamic Strategy Weight & Score Threshold Calibrator with strict safety boundary."""

    def __init__(
        self,
        learning_repo: LearningRepository,
        analytics_engine: AnalyticsEngine,
        bus: EventBus | None = None,
    ) -> None:
        self._learning_repo = learning_repo
        self._analytics_engine = analytics_engine
        self._bus = bus

    @staticmethod
    def validate_safety_boundary(proposed_overrides: dict[str, Any]) -> None:
        """
        Enforce B10 Learning Safety Boundary.
        Raises PermissionError if proposed calibration touches immutable safety invariants.
        """
        for key in proposed_overrides:
            if key in IMMUTABLE_SAFETY_BOUNDARIES:
                raise PermissionError(
                    f"Safety Violation: Learning service cannot mutate immutable invariant '{key}'"
                )

    async def calibrate_all_strategies(
        self, insights: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Evaluate performance metrics and insights for all production bots (STE, HDA, VCP, BBS)
        and update their strategy calibrations in SQLite.
        Returns list of updated calibration dictionaries.
        """
        metrics_summary = await self._analytics_engine.compute_performance_metrics()
        strategy_stats = metrics_summary.get("strategy_attribution", {})

        calibrations: list[dict[str, Any]] = []

        for bot_name in ("STE", "HDA", "VCP", "BBS"):
            bot_insights = [
                ins
                for ins in insights
                if str(ins.get("bot_name", "")).upper() == bot_name
            ]

            stats = strategy_stats.get(bot_name, {})
            win_rate = float(stats.get("win_rate_pct", 0.0))
            profit_factor = float(stats.get("profit_factor", 0.0))
            trades_count = int(stats.get("trades", 0))

            # Calibration decision matrix
            has_cooling_trigger = any(
                ins.get("pattern_type") == "CONSECUTIVE_LOSSES"
                or ins.get("severity") in ("HIGH", "CRITICAL")
                for ins in bot_insights
            )

            if has_cooling_trigger:
                status = "COOLING_DOWN"
                weight_multiplier = 0.5
                min_confluence_threshold = 90.0
            elif trades_count >= 3 and win_rate >= 70.0 and profit_factor >= 1.8:
                status = "BOOSTED"
                weight_multiplier = 1.2
                min_confluence_threshold = 80.0
            else:
                status = "ACTIVE"
                weight_multiplier = 1.0
                min_confluence_threshold = 85.0

            # Upsert into SQLite
            await self._learning_repo.upsert_calibration(
                bot_name=bot_name,
                pair=None,
                weight_multiplier=weight_multiplier,
                min_confluence_threshold=min_confluence_threshold,
                status=status,
            )

            cal_record = {
                "bot_name": bot_name,
                "status": status,
                "weight_multiplier": weight_multiplier,
                "min_confluence_threshold": min_confluence_threshold,
                "win_rate_pct": win_rate,
                "profit_factor": profit_factor,
                "insights_count": len(bot_insights),
            }
            calibrations.append(cal_record)

            # Publish EventBus events
            if self._bus:
                for ins in bot_insights:
                    await self._bus.publish(
                        EventType.ALERT_GENERATED,
                        ins,
                    )

                await self._bus.publish(
                    EventType.CALIBRATION_UPDATED,
                    cal_record,
                )

            logger.info(
                "Calibrated bot %s -> Status: %s | Weight: %.2fx | Min Score: %.1f",
                bot_name,
                status,
                weight_multiplier,
                min_confluence_threshold,
            )

        return calibrations
