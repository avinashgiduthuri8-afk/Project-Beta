"""
PROJECT-ALPHA Event Type Definitions.

Event names used by the PROJECT-ALPHA event bus. Pure enum constants.
Import from this module to publish or subscribe to events.
"""

from enum import Enum


class EventType(str, Enum):
    # ── Signal lifecycle ────────────────────────────────────────────────────
    SIGNAL_GENERATED = "signal.generated"
    SIGNAL_UPDATED = "signal.updated"
    SIGNAL_EXPIRED = "signal.expired"
    SIGNAL_AI_EVALUATED = "signal.ai_evaluated"
    SIGNAL_AI_CONFIRMED = "signal.ai_confirmed"
    SIGNAL_AI_REJECTED = "signal.ai_rejected"

    # ── Position lifecycle ──────────────────────────────────────────────────
    POSITION_OPENED = "position.opened"
    POSITION_CLOSED = "position.closed"
    POSITION_UPDATED = "position.updated"

    # ── Risk / circuit-breaker ──────────────────────────────────────────────
    CAPITAL_LIMIT_HIT = "risk.capital_limit_hit"
    DRAWDOWN_LIMIT_HIT = "risk.drawdown_limit_hit"
    CIRCUIT_BREAKER_TRIGGERED = "risk.circuit_breaker_triggered"

    # ── Bot lifecycle ───────────────────────────────────────────────────────
    BOT_STARTED = "bot.started"
    BOT_STOPPED = "bot.stopped"
    BOT_ERROR = "bot.error"

    # ── Portfolio / metrics ─────────────────────────────────────────────────
    METRICS_UPDATED = "metrics.updated"
    PORTFOLIO_UPDATED = "portfolio.updated"
    ALERT_GENERATED = "alert.generated"

    # ── NEW V2.1: Trade lifecycle ────────────────────────────────────────────
    TRADE_APPROVED = "trade.approved"  # RiskService: capital check passed
    TRADE_DENIED = "trade.denied"  # RiskService: capital check failed
    TRADE_EXECUTED = "trade.executed"  # TradingService: order placed
    TRADE_CLOSED = "trade.closed"  # TradingService: position fully exited

    # ── NEW V2.1: Scheduler ──────────────────────────────────────────────────
    JOB_STARTED = "scheduler.job_started"
    JOB_COMPLETED = "scheduler.job_completed"
    JOB_FAILED = "scheduler.job_failed"

    # ── NEW V2.1: System ─────────────────────────────────────────────────────
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    HEALTH_DEGRADED = "system.health_degraded"
    HEALTH_RECOVERED = "system.health_recovered"

    # ── NEW V2.1: Configuration ──────────────────────────────────────────────
    TRADING_ENABLED = "config.trading_enabled"
    TRADING_DISABLED = "config.trading_disabled"
    EMERGENCY_STOP_TRIGGERED = "config.emergency_stop"

    # ── Phase 6: Shadow Mode & Divergence ─────────────────────────────────────
    SHADOW_TRADE_RECORDED = "shadow.trade_recorded"
    SHADOW_TRADE_CLOSED = "shadow.trade_closed"
    DIVERGENCE_DETECTED = "divergence.detected"

    # ── Dynamic Calibration ──────────────────────────────────────────────────
    CALIBRATION_UPDATED = "calibration.updated"

    # ── Public Market Data Stream ────────────────────────────────────────────
    MARKET_DATA_UPDATED = "market_data.updated"
    ORDERBOOK_UPDATED = "orderbook.updated"
    TICKER_PRICE_UPDATE = "ticker.price_update"

    # ── AI Intelligence Circuit Breaker ──────────────────────────────────────
    AI_CIRCUIT_OPENED = "ai.circuit_opened"
    AI_CIRCUIT_CLOSED = "ai.circuit_closed"
