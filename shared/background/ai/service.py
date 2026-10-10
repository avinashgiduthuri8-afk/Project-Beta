"""
V2 AI Intelligence Service.

Acts as an independent quantitative and LLM-assisted confirmation gate
between the Market Scanner and Trade Construction / Risk Engine.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from core.repository.ai_repo import AIAnalysisRepository
from core.repository.event_log_repo import EventLogRepository
from core.repository.signal_repo import SignalRepository
from core.types import AIAnalysis, AIRecommendation, Priority, Signal

from .circuit_breaker import CircuitBreaker
from .client import GeminiClient
from .evaluator import FallbackEvaluator

logger = get_logger("background.ai.service")


class AIIntelligenceService:
    """Coordinates AI-driven signal evaluation, confirmation gating, and persistence."""

    def __init__(
        self,
        bus: EventBus,
        ai_repo: AIAnalysisRepository,
        event_log_repo: EventLogRepository,
        config: AppConfig,
        signal_repo: SignalRepository | None = None,
    ) -> None:
        self._bus = bus
        self._ai_repo = ai_repo
        self._event_log = event_log_repo
        self._config = config
        self._signal_repo = signal_repo

        self._client: GeminiClient | None = None
        if self._config.gemini_api_key:
            self._client = GeminiClient(
                api_key=self._config.gemini_api_key,
                model=self._config.ai_model,
                timeout_seconds=self._config.ai_timeout_seconds,
                max_retries=self._config.ai_max_retries,
            )

        cb_threshold = getattr(self._config, "ai_circuit_breaker_threshold", 3)
        cb_cooldown = getattr(self._config, "ai_circuit_breaker_cooldown_seconds", 60.0)
        self._circuit_breaker = CircuitBreaker(
            threshold=cb_threshold, cooldown_seconds=cb_cooldown
        )

        self._min_priority = Priority(self._config.ai_min_priority)
        self._total_evaluations = 0
        self._confirmed_count = 0
        self._rejected_count = 0
        self._fallback_count = 0
        self._latencies: list[float] = []
        self._last_error: str | None = None
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe event bus handlers."""
        if self._started:
            return
        self._started = True
        self._bus.subscribe(EventType.SIGNAL_GENERATED, self.on_signal_generated)
        await self._bus.publish(
            EventType.SYSTEM_STARTUP,
            {"service": "ai_intelligence_service", "model": self._config.ai_model},
        )
        logger.info(
            "AIIntelligenceService started", extra={"model": self._config.ai_model}
        )

    async def stop(self) -> None:
        """Unsubscribe handlers."""
        self._started = False
        self._bus.unsubscribe(EventType.SIGNAL_GENERATED, self.on_signal_generated)
        logger.info("AIIntelligenceService stopped")

    # ── Signal Evaluation Core ────────────────────────────────────────────────

    async def evaluate_signal(self, signal: Signal) -> AIAnalysis:
        """
        Evaluate a candidate signal through Gemini API or Fallback Evaluator,
        persist analysis, and publish confirmation/rejection events.
        """
        t0 = time.perf_counter()
        analysis: AIAnalysis | None = None
        used_fallback = False

        allowed = self._circuit_breaker.allow_request()

        if self._config.ai_enabled and self._client is not None and allowed:
            try:
                analysis = await self._client.evaluate_signal(signal)
                if self._circuit_breaker.record_success():
                    # Circuit transitioned from HALF_OPEN to CLOSED
                    await self._bus.publish(
                        EventType.AI_CIRCUIT_CLOSED,
                        {
                            "state": "CLOSED",
                            "reason": "Probe call succeeded; circuit recovered",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning(
                    "Gemini evaluation failed; falling back to heuristic evaluator",
                    extra={"coin": signal.coin, "error": str(exc)},
                )
                tripped = self._circuit_breaker.record_failure(str(exc))
                if tripped:
                    await self._bus.publish(
                        EventType.AI_CIRCUIT_OPENED,
                        {
                            "state": "OPEN",
                            "reason": str(exc),
                            "threshold": self._circuit_breaker.threshold,
                            "cooldown_seconds": self._circuit_breaker.cooldown_seconds,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                used_fallback = True
                analysis = FallbackEvaluator.evaluate(signal, start_time=t0)
        else:
            if not allowed:
                logger.debug(
                    "AI Circuit Breaker is %s; skipping Gemini and applying immediate fallback for %s",
                    self._circuit_breaker.state.value,
                    signal.coin,
                )
            used_fallback = True
            analysis = FallbackEvaluator.evaluate(signal, start_time=t0)

        if used_fallback:
            self._fallback_count += 1

        self._total_evaluations += 1
        self._latencies.append(analysis.execution_latency_ms)
        if len(self._latencies) > 200:
            self._latencies.pop(0)

        # 1. Persist analysis
        await self._ai_repo.insert(analysis)

        # 2. Publish SIGNAL_AI_EVALUATED
        eval_payload = {
            "analysis_id": analysis.id,
            "signal_id": signal.id,
            "coin": signal.coin,
            "pair": signal.pair,
            "recommendation": analysis.recommendation.value,
            "confidence_score": analysis.confidence_score,
            "model_name": analysis.model_name,
            "setup_quality": analysis.setup_quality,
            "latency_ms": analysis.execution_latency_ms,
            "confluence_score": (signal.raw_payload or {}).get("confluence_score"),
            "dynamic_threshold": (signal.raw_payload or {}).get("dynamic_threshold"),
            "mtf_timeframes": (signal.raw_payload or {}).get(
                "mtf_timeframes", ["5m", "15m", "15m"]
            ),
            "expires_at": signal.expires_at.isoformat(),
        }
        await self._bus.publish(EventType.SIGNAL_AI_EVALUATED, eval_payload)

        # 3. Confirmation vs Rejection Gating
        is_confirmed = (
            analysis.recommendation
            in (AIRecommendation.APPROVE, AIRecommendation.SCALE_DOWN)
            and analysis.confidence_score >= self._config.ai_confidence_threshold
        )

        raw_p = signal.raw_payload or {}
        price = float(raw_p.get("price") or raw_p.get("close") or 0.0)
        if price <= 0.0:
            logger.warning("AI Service dropping signal %s for %s: price <= 0.0", signal.id, signal.coin)
            return

        bot = (
            signal.source_bot
            if signal.source_bot in ("STE", "HDA", "VCP", "BBS")
            else raw_p.get("bot", "STE")
        )

        if is_confirmed:
            self._confirmed_count += 1
            confirm_payload = {
                "signal_id": signal.id,
                "analysis_id": analysis.id,
                "coin": signal.coin,
                "pair": signal.pair,
                "price": price,
                "market_state": (
                    signal.market_state.value
                    if hasattr(signal.market_state, "value")
                    else str(signal.market_state)
                ),
                "opportunity_type": (
                    signal.opportunity_type.value
                    if hasattr(signal.opportunity_type, "value")
                    else str(signal.opportunity_type)
                ),
                "bot": bot,
                "recommendation": getattr(
                    analysis.recommendation, "value", str(analysis.recommendation)
                ),
                "confidence_score": getattr(analysis, "confidence_score", 0),
                "risk_score": getattr(analysis, "risk_score", 0),
                "trade_action": getattr(
                    getattr(analysis, "trade_action", analysis.recommendation),
                    "value",
                    str(getattr(analysis, "trade_action", analysis.recommendation)),
                ),
                "suggested_allocation_inr": getattr(
                    analysis, "suggested_allocation_inr", 200.0
                ),
                "rationale": getattr(
                    analysis, "rationale", getattr(analysis, "trend_evaluation", "")
                ),
                "setup_quality": getattr(analysis, "setup_quality", "Medium"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self._bus.publish(EventType.SIGNAL_AI_CONFIRMED, confirm_payload)
            logger.info(
                "Signal CONFIRMED by AI",
                extra={
                    "coin": signal.coin,
                    "confidence": getattr(analysis, "confidence_score", 0),
                    "rec": getattr(
                        analysis.recommendation, "value", str(analysis.recommendation)
                    ),
                },
            )
        else:
            self._rejected_count += 1
            reject_payload = {
                "signal_id": signal.id,
                "analysis_id": analysis.id,
                "coin": signal.coin,
                "pair": signal.pair,
                "price": price,
                "market_state": (
                    signal.market_state.value
                    if hasattr(signal.market_state, "value")
                    else str(signal.market_state)
                ),
                "opportunity_type": (
                    signal.opportunity_type.value
                    if hasattr(signal.opportunity_type, "value")
                    else str(signal.opportunity_type)
                ),
                "bot": bot,
                "recommendation": getattr(
                    analysis.recommendation, "value", str(analysis.recommendation)
                ),
                "confidence_score": getattr(analysis, "confidence_score", 0),
                "risk_score": getattr(analysis, "risk_score", 0),
                "trade_action": getattr(
                    getattr(analysis, "trade_action", analysis.recommendation),
                    "value",
                    str(getattr(analysis, "trade_action", analysis.recommendation)),
                ),
                "rationale": getattr(
                    analysis, "rationale", getattr(analysis, "trend_evaluation", "")
                ),
                "setup_quality": getattr(analysis, "setup_quality", "Medium"),
                "rejection_reason": (
                    f"AI recommendation={getattr(analysis.recommendation, 'value', str(analysis.recommendation))}, "
                    f"confidence={getattr(analysis, 'confidence_score', 0)} "
                    f"(min={self._config.ai_confidence_threshold})"
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self._bus.publish(EventType.SIGNAL_AI_REJECTED, reject_payload)
            logger.info(
                "Signal REJECTED by AI",
                extra={
                    "coin": signal.coin,
                    "confidence": analysis.confidence_score,
                    "rec": analysis.recommendation.value,
                },
            )

        # 4. Record Event Log
        await self._event_log.log_event(
            (
                EventType.SIGNAL_AI_CONFIRMED
                if is_confirmed
                else EventType.SIGNAL_AI_REJECTED
            ),
            {
                "coin": signal.coin,
                "signal_id": signal.id,
                "analysis_id": analysis.id,
                "recommendation": analysis.recommendation.value,
                "confidence": analysis.confidence_score,
                "used_fallback": used_fallback,
            },
            source_service="ai_intelligence_service",
        )

        return analysis

    # ── Bus Event Handlers ────────────────────────────────────────────────

    async def on_signal_generated(self, event_type: EventType, payload: dict) -> None:
        """Handle incoming signal from scanner and evaluate if priority criteria is met."""
        try:
            signal_id = payload.get("signal_id") or payload.get("id")
            if not signal_id and self._signal_repo:
                return

            signal: Signal | None = None
            if self._signal_repo and signal_id:
                signal = await self._signal_repo.get_by_id(signal_id)

            if signal is None:
                # Construct temporary Signal from payload dictionary
                from scanner.adapter import raw_signal_to_domain

                signal = raw_signal_to_domain(payload)

            if signal.priority.gte(self._min_priority):
                await self.evaluate_signal(signal)
        except Exception as exc:
            self._last_error = str(exc)
            logger.error(
                "Error processing SIGNAL_GENERATED in AIIntelligenceService",
                exc_info=True,
            )

    # ── Telemetry & Health ────────────────────────────────────────────────────

    def get_health(self) -> dict:
        """Return health, latency, and throughput statistics."""
        avg_lat = (
            round(sum(self._latencies) / len(self._latencies), 2)
            if self._latencies
            else 0.0
        )
        return {
            "healthy": self._started,
            "ai_enabled": self._config.ai_enabled,
            "model": self._config.ai_model,
            "has_api_key": bool(self._config.gemini_api_key),
            "min_priority": self._min_priority.value,
            "confidence_threshold": self._config.ai_confidence_threshold,
            "total_evaluations": self._total_evaluations,
            "confirmed_count": self._confirmed_count,
            "rejected_count": self._rejected_count,
            "fallback_count": self._fallback_count,
            "avg_latency_ms": avg_lat,
            "last_error": self._last_error,
            "circuit_breaker": self._circuit_breaker.get_state(),
        }

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Access the underlying circuit breaker instance."""
        return self._circuit_breaker
