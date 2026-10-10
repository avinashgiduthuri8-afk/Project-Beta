"""
V2 Scheduler Job Definitions.

All jobs are defined as thin wrappers that delegate to the
relevant service. The BackgroundScheduler owns the timing;
the services own the logic.

Call register_all_jobs() at application startup after all services
are initialised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import AppConfig
from core.logging import get_logger
from scanner import ScannerService

from .scheduler import BackgroundScheduler

if TYPE_CHECKING:
    from execution import TradingService

logger = get_logger("background.scheduler.jobs")


def register_all_jobs(
    scheduler: BackgroundScheduler,
    config: AppConfig,
    scanner_service: ScannerService,
    trading_service: TradingService | None = None,
) -> None:
    """
    Register all V2.1 scheduler jobs.

    Additional jobs will be added in V2.2–V2.5 as services come online.
    Register all V2 scheduler jobs.
    Register all scheduler jobs.
    """

    # ── scanner_poll ──────────────────────────────────────────────────────────
    # Polls V1 scanner API, publishes SIGNAL_GENERATED / SIGNAL_EXPIRED events.
    if scanner_service is not None:
        scheduler.register(
            name="scanner_poll",
            fn=scanner_service.poll,
            interval=config.scanner_poll_interval,
            enabled=True,
        )

        # ── signal_expiry_check ───────────────────────────────────────────────────
        # Secondary sweep for signals that slipped past the poll-cycle expiry check.
        scheduler.register(
            name="signal_expiry_check",
            fn=scanner_service.check_expiry,
            interval=30,
            enabled=True,
        )

    logger.info("All background scheduler jobs registered")
    # ── exit_monitor ──────────────────────────────────────────────────────────
    # Periodic sweep for open positions hitting SL / TP / Trailing exit triggers.
    if trading_service is not None:
        scheduler.register(
            name="exit_monitor",
            fn=trading_service.poll_exits,
            interval=5,
            enabled=True,
        )

        # ── order_reconciliation ──────────────────────────────────────────────
        # Periodic reconciliation comparing local DB positions with CoinDCX exchange state.
        scheduler.register(
            name="order_reconciliation",
            fn=trading_service.reconcile_live_orders,
            interval=60,
            enabled=True,
        )

    logger.info(
        "All V2 scheduler jobs registered (including exit_monitor and order_reconciliation)"
    )
