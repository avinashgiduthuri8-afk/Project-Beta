"""
V2 Background Scheduler.

Runs named async jobs at fixed intervals inside the asyncio event loop.
Publishes JOB_STARTED / JOB_COMPLETED / JOB_FAILED events on the bus.

Failure policy:
  - A failed job logs the error and publishes JOB_FAILED — it does NOT crash.
  - After MAX_CONSECUTIVE_ERRORS consecutive failures, the job is auto-disabled
    and an ALERT_GENERATED event is published.
  - The scheduler loop itself is crash-resilient: any uncaught exception
    restarts the loop after LOOP_RESTART_DELAY_S seconds.
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from v2.bus.event_bus import EventBus
from v2.bus.event_types import EventType
from v2.core.logging import get_logger

logger = get_logger("v2.scheduler")

MAX_CONSECUTIVE_ERRORS = 5
LOOP_RESTART_DELAY_S = 5


@dataclass
class JobDefinition:
    name:               str
    fn:                 Callable[[], Awaitable[Any]]
    interval:           int            # seconds
    enabled:            bool = True
    last_run_at:        Optional[datetime] = None
    last_duration_ms:   Optional[int] = None
    last_error:         Optional[str] = None
    run_count:          int = 0
    error_count:        int = 0
    consecutive_errors: int = 0


class BackgroundScheduler:
    """
    Lightweight asyncio-based job scheduler.

    Usage:
        scheduler = BackgroundScheduler(bus)
        scheduler.register("my_job", my_async_fn, interval=60)
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._jobs: dict[str, JobDefinition] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Per-job in-flight guard: job name → running asyncio.Task.
        # _tick() skips scheduling a new run if the job is still in flight.
        self._inflight: dict[str, asyncio.Task] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        fn: Callable[[], Awaitable[Any]],
        interval: int,
        enabled: bool = True,
    ) -> None:
        """Register a named job. Safe to call before start()."""
        if name in self._jobs:
            logger.warning("Job already registered — overwriting", extra={"job": name})
        self._jobs[name] = JobDefinition(name=name, fn=fn, interval=interval, enabled=enabled)
        logger.info("Job registered", extra={"job": name, "interval": interval, "enabled": enabled})

    def enable(self, name: str) -> None:
        if name in self._jobs:
            self._jobs[name].enabled = True
            self._jobs[name].consecutive_errors = 0
            logger.info("Job enabled", extra={"job": name})

    def disable(self, name: str) -> None:
        if name in self._jobs:
            self._jobs[name].enabled = False
            logger.info("Job disabled", extra={"job": name})

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="v2_scheduler")
        logger.info("Scheduler started", extra={"jobs": list(self._jobs.keys())})

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Drain all in-flight job tasks before returning so callers (e.g.
        # lifespan shutdown) can safely close the DB right after stop().
        inflight_tasks = [t for t in self._inflight.values() if not t.done()]
        if inflight_tasks:
            logger.info(
                "Draining in-flight jobs",
                extra={"count": len(inflight_tasks)},
            )
            for t in inflight_tasks:
                t.cancel()
            await asyncio.gather(*inflight_tasks, return_exceptions=True)
        self._inflight.clear()
        logger.info("Scheduler stopped")

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> list[dict]:
        return [
            {
                "name":             j.name,
                "enabled":          j.enabled,
                "interval_s":       j.interval,
                "run_count":        j.run_count,
                "error_count":      j.error_count,
                "consecutive_errors": j.consecutive_errors,
                "last_run_at":      j.last_run_at.isoformat() if j.last_run_at else None,
                "last_duration_ms": j.last_duration_ms,
                "last_error":       j.last_error,
            }
            for j in self._jobs.values()
        ]

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """
        Main scheduler loop. Ticks every second and fires due jobs.
        Crashes in this loop restart after LOOP_RESTART_DELAY_S seconds.
        """
        while self._running:
            try:
                await self._tick()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Scheduler loop crashed — restarting", extra={"error": str(exc)})
                await self._bus.publish(
                    EventType.BOT_ERROR,
                    {"bot": "scheduler", "error_type": type(exc).__name__, "message": str(exc)},
                )
                await asyncio.sleep(LOOP_RESTART_DELAY_S)

    async def _tick(self) -> None:
        """Check each job and run it if its interval has elapsed."""
        now = datetime.now(timezone.utc)
        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            # Skip if a previous run is still in flight (prevents overlap).
            existing = self._inflight.get(job.name)
            if existing and not existing.done():
                continue
            if job.last_run_at is None or (
                (now - job.last_run_at).total_seconds() >= job.interval
            ):
                task = asyncio.create_task(self._run_job(job), name=f"job_{job.name}")
                self._inflight[job.name] = task

    async def _run_job(self, job: JobDefinition) -> None:
        """Execute a single job, measure duration, handle errors."""
        start = datetime.now(timezone.utc)
        # Set last_run_at BEFORE awaiting fn() so the scheduler's _tick()
        # does not create a duplicate task while this one is still running.
        job.last_run_at = start

        await self._bus.publish(
            EventType.JOB_STARTED,
            {"job": job.name, "started_at": start.isoformat()},
        )

        try:
            result = await job.fn()
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

            job.run_count += 1
            job.last_duration_ms = duration_ms
            job.last_error = None
            job.consecutive_errors = 0

            await self._bus.publish(
                EventType.JOB_COMPLETED,
                {
                    "job":         job.name,
                    "duration_ms": duration_ms,
                    "result":      str(result)[:200] if result is not None else None,
                },
            )
            logger.info(
                "Job completed",
                extra={"job": job.name, "duration_ms": duration_ms},
            )

        except Exception as exc:
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            tb = traceback.format_exc()

            job.run_count += 1
            job.last_duration_ms = duration_ms
            job.last_error = str(exc)
            job.error_count += 1
            job.consecutive_errors += 1

            logger.error(
                "Job failed",
                extra={"job": job.name, "error": str(exc), "consecutive": job.consecutive_errors},
            )

            await self._bus.publish(
                EventType.JOB_FAILED,
                {
                    "job":         job.name,
                    "error":       str(exc),
                    "consecutive": job.consecutive_errors,
                    "traceback":   tb[:500],
                },
            )

            # Auto-disable after too many consecutive failures
            if job.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                job.enabled = False
                logger.error(
                    "Job auto-disabled after consecutive failures",
                    extra={"job": job.name, "errors": job.consecutive_errors},
                )
                await self._bus.publish(
                    EventType.ALERT_GENERATED,
                    {
                        "level":     "WARN",
                        "title":     f"Scheduler job '{job.name}' auto-disabled",
                        "body":      f"Failed {job.consecutive_errors}× consecutively. Last error: {exc}",
                        "event_ref": "JOB_FAILED",
                    },
                )
