"""
V2 Autonomous Feedback Orchestrator.

Orchestrates the recursive feedback pipeline:
  Signal -> Trade Execution -> Journal Result -> Learning Insight -> Pre-Validation Backtest -> Calibration Promotion/Rollback.

Enforces:
  1. Pre-Deployment Backtest Validation Gate (rejects parameter changes causing performance degradation).
  2. Promotion Gate to Active Cache (promotes validated strategy weights & confluence score thresholds).
  3. Safety Rollback Engine (initiates emergency rollback to baseline config on 2 consecutive post-promotion losses).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from background.backtest.service.service import BacktestService
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.feedback_repo import FeedbackRepository

logger = get_logger("background.feedback.orchestrator")


class FeedbackOrchestrator:
    """Autonomous Feedback Pipeline Orchestrator & Promotion Gate."""

    def __init__(
        self,
        feedback_repo: FeedbackRepository,
        backtest_service: BacktestService,
        bus: EventBus | None = None,
    ) -> None:
        self._feedback_repo = feedback_repo
        self._backtest_service = backtest_service
        self._bus = bus

    async def evaluate_and_validate_calibration(
        self,
        bot_name: str,
        pair: str,
        proposed_multiplier: float,
        proposed_threshold: float,
        validation_candles: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Pre-Deployment Safety Validation & Promotion Gate:
          1. Retrieve current active calibration baseline.
          2. Execute backtest simulation using candidate parameters.
          3. Promote if metrics hold or improve; reject if metrics degrade.
          4. Persist audit trail event to SQLite.
        """
        cycle_id = str(uuid.uuid4())
        bot_str = bot_name.upper()

        # Fetch current active baseline from cache
        active_curr = await self._feedback_repo.get_active_calibration(bot_str)
        prev_mult = float(active_curr["weight_multiplier"]) if active_curr else 1.0
        prev_thresh = float(active_curr["strict_threshold"]) if active_curr else 85.0

        # Action classification
        if proposed_multiplier > prev_mult:
            action = "WEIGHT_BOOST"
        elif proposed_multiplier < prev_mult:
            action = "WEIGHT_PENALTY"
        elif proposed_threshold > prev_thresh:
            action = "THRESHOLD_TIGHTEN"
        else:
            action = "WEIGHT_PENALTY"

        # Re-running an identical calibration must not reset an active canary's
        # consecutive-loss counter or promotion timestamp.
        if active_curr and (
            abs(proposed_multiplier - prev_mult) < 1e-9
            and abs(proposed_threshold - prev_thresh) < 1e-9
        ):
            unchanged_event = {
                "id": str(uuid.uuid4()),
                "cycle_id": cycle_id,
                "bot_name": bot_str,
                "pair": pair,
                "action_taken": "NO_CHANGE",
                "previous_multiplier": prev_mult,
                "new_multiplier": prev_mult,
                "previous_threshold": prev_thresh,
                "new_threshold": prev_thresh,
                "validation_backtest_id": None,
                "status": "UNCHANGED",
                "validation_candle_count": len(validation_candles or []),
                "validation_oos_candle_count": 0,
                "validation_trade_count": 0,
                "validation_profit_factor": None,
                "validation_reason": "NO_PARAMETER_CHANGE",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await self._feedback_repo.record_audit_event(unchanged_event)
            return unchanged_event

        # 1. Pre-Deployment walk-forward verification.  A candidate is never
        # promoted without enough authentic candles and executed OOS trades.
        candles = sorted(
            validation_candles or [], key=lambda c: str(c.get("timestamp", ""))
        )
        parameters = {
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.046,
            "min_confluence_threshold": proposed_threshold,
            "multiplier": proposed_multiplier,
        }

        minimum_candles = 60
        if len(candles) >= minimum_candles:
            _, out_of_sample = self._backtest_service.optimizer.walk_forward_split(
                candles, train_ratio=0.70
            )
            parameters["timeframe"] = "1h"
            parameters["validation_segment"] = "OUT_OF_SAMPLE"
            backtest_res = await self._backtest_service.run_backtest(
                strategy_name=bot_str,
                pair=pair,
                candles=out_of_sample,
                parameters=parameters,
            )
        else:
            out_of_sample = []
            backtest_res = {
                "id": None,
                "profit_factor": 0.0,
                "total_trades": 0,
                "trades_count": 0,
            }

        validation_backtest_id = backtest_res.get("id")
        profit_factor = float(backtest_res.get("profit_factor", 0.0))

        # Promotion Gate Criteria: reject missing evidence, then enforce PF >= 0.8.
        trades_count = int(backtest_res.get("total_trades", 0))
        sufficient_evidence = len(candles) >= minimum_candles and trades_count >= 2
        status = (
            "PROMOTED"
            if sufficient_evidence and profit_factor >= 0.8
            else "REJECTED"
        )

        audit_event = {
            "id": str(uuid.uuid4()),
            "cycle_id": cycle_id,
            "bot_name": bot_str,
            "pair": pair,
            "action_taken": action,
            "previous_multiplier": prev_mult,
            "new_multiplier": (
                proposed_multiplier if status == "PROMOTED" else prev_mult
            ),
            "previous_threshold": prev_thresh,
            "new_threshold": (
                proposed_threshold if status == "PROMOTED" else prev_thresh
            ),
            "validation_backtest_id": validation_backtest_id,
            "status": status,
            "validation_candle_count": len(candles),
            "validation_oos_candle_count": len(out_of_sample),
            "validation_trade_count": trades_count,
            "validation_profit_factor": profit_factor,
            "validation_reason": (
                "PASSED"
                if status == "PROMOTED"
                else (
                    "INSUFFICIENT_VALIDATION_DATA"
                    if not sufficient_evidence
                    else "PROFIT_FACTOR_BELOW_0.8"
                )
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist audit trail event
        await self._feedback_repo.record_audit_event(audit_event)

        if status == "PROMOTED":
            # Update Active Calibrations Cache
            await self._feedback_repo.upsert_active_calibration(
                bot_name=bot_str,
                weight_multiplier=proposed_multiplier,
                strict_threshold=proposed_threshold,
            )
            await self._feedback_repo.activate_canary(
                bot_name=bot_str,
                promotion_id=audit_event["id"],
                promoted_at=audit_event["created_at"],
            )

            if self._bus:
                await self._bus.publish(
                    EventType.CALIBRATION_UPDATED,
                    audit_event,
                )

            logger.info(
                "PROMOTED new calibration for %s -> Multiplier: %.2fx, Threshold: %.1f",
                bot_str,
                proposed_multiplier,
                proposed_threshold,
            )
        else:
            logger.warning(
                "REJECTED candidate calibration for %s due to backtest degradation (PF: %.2f)",
                bot_str,
                profit_factor,
            )

        return audit_event

    async def register_trade_outcome(
        self,
        bot_name: str,
        pair: str,
        is_win: bool,
        trade_id: str | None = None,
        exited_at: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Safety Rollback Engine:
          Monitors trade outcomes for bots post-promotion.
          If 2 consecutive losses occur immediately following promotion,
          initiates emergency rollback to baseline configuration (weight=1.0, threshold=85.0).
        """
        bot_str = bot_name.upper()

        state = await self._feedback_repo.get_canary_state(bot_str)
        if not state:
            return None

        if exited_at:
            try:
                exit_dt = datetime.fromisoformat(str(exited_at).replace("Z", "+00:00"))
                promoted_dt = datetime.fromisoformat(
                    str(state["promoted_at"]).replace("Z", "+00:00")
                )
                if exit_dt.tzinfo is None:
                    exit_dt = exit_dt.replace(tzinfo=timezone.utc)
                if promoted_dt.tzinfo is None:
                    promoted_dt = promoted_dt.replace(tzinfo=timezone.utc)
                if exit_dt <= promoted_dt:
                    return None
            except (TypeError, ValueError):
                logger.warning("Ignoring outcome with invalid exit timestamp: %s", exited_at)
                return None

        outcome_id = trade_id or str(uuid.uuid4())
        claimed = await self._feedback_repo.claim_calibration_outcome(
            outcome_id, bot_str, str(state["promotion_id"])
        )
        if not claimed:
            return None

        if is_win:
            await self._feedback_repo.update_canary_losses(bot_str, 0)
            return None

        # Increment loss counter on losing trade
        current_losses = int(state.get("consecutive_losses", 0)) + 1
        await self._feedback_repo.update_canary_losses(bot_str, current_losses)

        if current_losses >= 2:
            # Trigger Safety Rollback
            logger.warning(
                "Safety Rollback triggered for bot %s after %d consecutive post-promotion losses",
                bot_str,
                current_losses,
            )

            active_curr = await self._feedback_repo.get_active_calibration(bot_str)
            prev_mult = float(active_curr["weight_multiplier"]) if active_curr else 1.0
            prev_thresh = (
                float(active_curr["strict_threshold"]) if active_curr else 85.0
            )

            baseline_mult = 1.0
            baseline_thresh = 85.0

            rollback_event = {
                "id": str(uuid.uuid4()),
                "cycle_id": str(uuid.uuid4()),
                "bot_name": bot_str,
                "pair": pair,
                "action_taken": "ROLLBACK",
                "previous_multiplier": prev_mult,
                "new_multiplier": baseline_mult,
                "previous_threshold": prev_thresh,
                "new_threshold": baseline_thresh,
                "validation_backtest_id": None,
                "status": "ROLLED_BACK",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            await self._feedback_repo.record_audit_event(rollback_event)
            await self._feedback_repo.upsert_active_calibration(
                bot_name=bot_str,
                weight_multiplier=baseline_mult,
                strict_threshold=baseline_thresh,
            )

            await self._feedback_repo.update_canary_losses(
                bot_str, 0, active=False
            )
            if self._bus:
                await self._bus.publish(
                    EventType.CALIBRATION_UPDATED,
                    rollback_event,
                )

            return rollback_event

        return None
