"""
V2 Application Entry Point.

Runs a standalone FastAPI server on V2_PORT (default 5001).
V1 app.py on port 5000 is completely untouched.

Startup sequence:
  1. Load V2Config
  2. Open SQLite database (apply migrations)
  3. Initialise repositories
  4. Initialise ScannerService
  5. Start BackgroundScheduler + register jobs
  6. Wire API router
  7. Serve on V2_PORT

Shutdown sequence (lifespan):
  1. Stop scheduler
  2. Stop scanner service
  3. Close database

Run:
    python v2/app_v2.py
    # or via workflow: python -m v2.app_v2
"""

from __future__ import annotations

import asyncio
import sys
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

# ── Ensure project root is on sys.path when run directly ─────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v2.core.config import get_config
from v2.core.logging import get_logger
from v2.bus import bus
from v2.repository.db import Database
from v2.repository.signal_repo import SignalRepository
from v2.repository.event_log_repo import EventLogRepository
from v2.services.scanner_service import ScannerService
from v2.scheduler import BackgroundScheduler, register_all_jobs
from v2.api.router import router as api_router, init_router
from v2.bus.subscribers import register_all as register_all_subscribers

logger = get_logger("v2.app")

# ── Module-level service singletons (assigned in lifespan) ────────────────────
_db: Database | None = None
_scanner_service: ScannerService | None = None
_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup then shutdown."""
    global _db, _scanner_service, _scheduler

    cfg = get_config()
    logger.info("V2 starting", extra={"port": cfg.v2_port, "db": cfg.v2_db_path})

    # 1. Database
    _db = Database(cfg.v2_db_path)
    await _db.open()

    # 2. Repositories
    conn = _db.connection
    signal_repo    = SignalRepository(conn)
    event_log_repo = EventLogRepository(conn)

    # 3. Scanner service
    _scanner_service = ScannerService(
        bus            = bus,
        signal_repo    = signal_repo,
        event_log_repo = event_log_repo,
        config         = cfg,
    )
    await _scanner_service.start()

    # 4. Scheduler
    _scheduler = BackgroundScheduler(bus)
    register_all_jobs(
        scheduler       = _scheduler,
        config          = cfg,
        scanner_service = _scanner_service,
    )
    await _scheduler.start()

    # 5. Wire subscriber registry
    register_all_subscribers(bus, scanner_service=_scanner_service)

    # 6. Wire API router state
    init_router(
        scanner_service = _scanner_service,
        scheduler       = _scheduler,
        config          = cfg,
    )

    logger.info("V2 startup complete")

    yield  # ── application is running ──────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("V2 shutting down")
    if _scheduler:
        await _scheduler.stop()
    if _scanner_service:
        await _scanner_service.stop()
    if _db:
        await _db.close()
    logger.info("V2 shutdown complete")


# ── FastAPI application ───────────────────────────────────────────────────────

app = FastAPI(
    title       = "PROJECT-ALPHA V2",
    description = "V2 event-driven trading infrastructure",
    version     = "2.1.0",
    lifespan    = lifespan,
    docs_url    = "/api/v2/docs",
    redoc_url   = "/api/v2/redoc",
    openapi_url = "/api/v2/openapi.json",
)

app.include_router(api_router, prefix="/api/v2")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = get_config()
    uvicorn.run(
        "v2.app_v2:app",
        host       = cfg.v2_host,
        port       = cfg.v2_port,
        log_level  = "info",
        access_log = True,
    )
