"""
V2 Scheduler Job Definitions.

All jobs are defined as thin wrappers that delegate to the
relevant service. The BackgroundScheduler owns the timing;
the services own the logic.

Call register_all_jobs() at application startup after all services
are initialised.
"""

from __future__ import annotations

from v2.core.config import V2Config
from v2.core.logging import get_logger
from v2.services.scanner_service import ScannerService
from .scheduler import BackgroundScheduler

logger = get_logger("v2.scheduler.jobs")


def register_all_jobs(
    scheduler: BackgroundScheduler,
    config: V2Config,
    scanner_service: ScannerService,
) -> None:
    """
    Register all V2.1 scheduler jobs.

    Additional jobs will be added in V2.2–V2.5 as services come online.
    """

    # ── scanner_poll ──────────────────────────────────────────────────────────
    # Polls V1 scanner API, publishes SIGNAL_GENERATED / SIGNAL_EXPIRED events.
    scheduler.register(
        name     = "scanner_poll",
        fn       = scanner_service.poll,
        interval = config.v2_scanner_poll_interval,
        enabled  = True,
    )

    # ── signal_expiry_check ───────────────────────────────────────────────────
    # Secondary sweep for signals that slipped past the poll-cycle expiry check.
    scheduler.register(
        name     = "signal_expiry_check",
        fn       = scanner_service.check_expiry,
        interval = 30,
        enabled  = True,
    )

    logger.info("All V2.1 jobs registered")
