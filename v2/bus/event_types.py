"""
V2 Event Type Definitions.

All 28 event names used by the V2 event bus. No logic here — pure constants.
Import from this module to publish or subscribe to events.

Changelog:
  V2.0: 16 original events
  V2.1: +12 events (trade lifecycle, scheduler, system, config)
"""

from enum import Enum


class EventType(str, Enum):

    # ── Signal lifecycle ────────────────────────────────────────────────────
    SIGNAL_GENERATED  = "signal.generated"
    SIGNAL_UPDATED    = "signal.updated"
    SIGNAL_EXPIRED    = "signal.expired"

    # ── Position lifecycle ──────────────────────────────────────────────────
    POSITION_OPENED   = "position.opened"
    POSITION_CLOSED   = "position.closed"
    POSITION_UPDATED  = "position.updated"

    # ── Risk / circuit-breaker ──────────────────────────────────────────────
    CAPITAL_LIMIT_HIT         = "risk.capital_limit_hit"
    DRAWDOWN_LIMIT_HIT        = "risk.drawdown_limit_hit"
    CIRCUIT_BREAKER_TRIGGERED = "risk.circuit_breaker_triggered"

    # ── Bot lifecycle ───────────────────────────────────────────────────────
    BOT_STARTED = "bot.started"
    BOT_STOPPED = "bot.stopped"
    BOT_ERROR   = "bot.error"

    # ── Portfolio / metrics ─────────────────────────────────────────────────
    METRICS_UPDATED   = "metrics.updated"
    PORTFOLIO_UPDATED = "portfolio.updated"
    ALERT_GENERATED   = "alert.generated"

    # ── NEW V2.1: Trade lifecycle ────────────────────────────────────────────
    TRADE_APPROVED = "trade.approved"    # RiskService: capital check passed
    TRADE_DENIED   = "trade.denied"      # RiskService: capital check failed
    TRADE_EXECUTED = "trade.executed"    # TradingService: order placed
    TRADE_CLOSED   = "trade.closed"      # TradingService: position fully exited

    # ── NEW V2.1: Scheduler ──────────────────────────────────────────────────
    JOB_STARTED   = "scheduler.job_started"
    JOB_COMPLETED = "scheduler.job_completed"
    JOB_FAILED    = "scheduler.job_failed"

    # ── NEW V2.1: System ─────────────────────────────────────────────────────
    SYSTEM_STARTUP    = "system.startup"
    SYSTEM_SHUTDOWN   = "system.shutdown"
    HEALTH_DEGRADED   = "system.health_degraded"
    HEALTH_RECOVERED  = "system.health_recovered"

    # ── NEW V2.1: Configuration ──────────────────────────────────────────────
    TRADING_ENABLED          = "config.trading_enabled"
    TRADING_DISABLED         = "config.trading_disabled"
    EMERGENCY_STOP_TRIGGERED = "config.emergency_stop"
