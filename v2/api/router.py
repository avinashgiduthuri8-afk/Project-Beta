"""
V2 API Router — all /api/v2/* endpoints.

Mounted in v2/app_v2.py under the prefix /api/v2.

V2.1 endpoints:
  GET /api/v2/health                  — liveness (no auth)
  GET /api/v2/status                  — full system status (auth required)
  GET /api/v2/scanner/signals         — live signal list (auth required)
  GET /api/v2/scanner/signals/{id}    — single signal (auth required)
  GET /api/v2/scanner/health          — scanner sub-health (auth required)
  GET /api/v2/scheduler/jobs          — scheduler job statuses (auth required)

Later phases add /api/v2/portfolio, /api/v2/risk, /api/v2/trades, etc.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from v2.core.types import Priority
from .auth import require_api_key
from .schemas import (
    OkSchema, JobStatusSchema, ScannerHealthSchema,
    SignalSchema, V2StatusSchema,
)

router = APIRouter()

# ── Injected service references (set by app_v2.py at startup) ────────────────
# Using module-level state to avoid circular imports. In V2.3+ these will be
# passed via FastAPI dependency injection with proper DI containers.

_scanner_service = None
_scheduler = None
_config = None


def init_router(scanner_service, scheduler, config) -> None:
    """Called by app_v2.py lifespan after services are started."""
    global _scanner_service, _scheduler, _config
    _scanner_service = scanner_service
    _scheduler = scheduler
    _config = config


# ── Health (no auth) ──────────────────────────────────────────────────────────

@router.get("/health", response_model=OkSchema, tags=["system"])
async def health() -> OkSchema:
    """Liveness probe — always returns 200 if V2 process is alive."""
    return OkSchema(ok=True, detail="V2 running")


# ── Status (auth required) ────────────────────────────────────────────────────

@router.get(
    "/status",
    response_model=V2StatusSchema,
    dependencies=[Depends(require_api_key)],
    tags=["system"],
)
async def status_endpoint() -> V2StatusSchema:
    """Full V2 system status snapshot."""
    scanner_h = _scanner_service.get_health() if _scanner_service else {}
    jobs = _scheduler.get_status() if _scheduler else []

    return V2StatusSchema(
        scanner_health=ScannerHealthSchema(**scanner_h) if scanner_h else ScannerHealthSchema(
            healthy=False, poll_count=0, live_signals=0, last_poll_at=None, last_error="not started"
        ),
        scheduler_jobs=[JobStatusSchema(**j) for j in jobs],
        db_path=_config.v2_db_path if _config else "unknown",
        uptime_polls=scanner_h.get("poll_count", 0),
        live_signals=scanner_h.get("live_signals", 0),
    )


# ── Scanner endpoints ─────────────────────────────────────────────────────────

@router.get(
    "/scanner/signals",
    response_model=list[SignalSchema],
    dependencies=[Depends(require_api_key)],
    tags=["scanner"],
)
async def get_signals(
    priority: Optional[str] = Query(
        default=None,
        description="Minimum priority filter: Elite|High|Medium|Watch|Ignore",
    ),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[SignalSchema]:
    """Return current live signals, optionally filtered by minimum priority."""
    if _scanner_service is None:
        raise HTTPException(status_code=503, detail="Scanner service not ready.")

    signals = _scanner_service.get_live_signals()

    # Apply priority filter if requested
    if priority:
        try:
            min_p = Priority(priority)
            signals = [s for s in signals if s.priority.gte(min_p)]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid priority '{priority}'. "
                       f"Valid values: Elite, High, Medium, Watch, Ignore",
            )

    # Apply limit
    signals = signals[:limit]

    return [
        SignalSchema(
            id               = s.id,
            coin             = s.coin,
            pair             = s.pair,
            market_state     = s.market_state.value,
            opportunity_type = s.opportunity_type.value,
            priority         = s.priority.value,
            risk_level       = s.risk_level.value,
            score            = s.score,
            confidence       = s.confidence,
            coin_class       = s.coin_class,
            mtf_alignment    = s.mtf_alignment,
            generated_at     = s.generated_at,
            expires_at       = s.expires_at,
            source_bot       = s.source_bot,
        )
        for s in signals
    ]


@router.get(
    "/scanner/signals/{signal_id}",
    response_model=SignalSchema,
    dependencies=[Depends(require_api_key)],
    tags=["scanner"],
)
async def get_signal_by_id(signal_id: str) -> SignalSchema:
    """Return a single signal by ID (live cache only)."""
    if _scanner_service is None:
        raise HTTPException(status_code=503, detail="Scanner service not ready.")
    live = {s.id: s for s in _scanner_service.get_live_signals()}
    if signal_id not in live:
        raise HTTPException(status_code=404, detail=f"Signal '{signal_id}' not found in live cache.")
    s = live[signal_id]
    return SignalSchema(
        id=s.id, coin=s.coin, pair=s.pair,
        market_state=s.market_state.value, opportunity_type=s.opportunity_type.value,
        priority=s.priority.value, risk_level=s.risk_level.value,
        score=s.score, confidence=s.confidence, coin_class=s.coin_class,
        mtf_alignment=s.mtf_alignment, generated_at=s.generated_at,
        expires_at=s.expires_at, source_bot=s.source_bot,
    )


@router.get(
    "/scanner/health",
    response_model=ScannerHealthSchema,
    dependencies=[Depends(require_api_key)],
    tags=["scanner"],
)
async def scanner_health() -> ScannerHealthSchema:
    """Scanner sub-health: poll count, live signal count, last error."""
    if _scanner_service is None:
        return ScannerHealthSchema(
            healthy=False, poll_count=0, live_signals=0,
            last_poll_at=None, last_error="not started",
        )
    return ScannerHealthSchema(**_scanner_service.get_health())


# ── Scheduler endpoints ───────────────────────────────────────────────────────

@router.get(
    "/scheduler/jobs",
    response_model=list[JobStatusSchema],
    dependencies=[Depends(require_api_key)],
    tags=["scheduler"],
)
async def scheduler_jobs() -> list[JobStatusSchema]:
    """Return current status of all registered scheduler jobs."""
    if _scheduler is None:
        return []
    return [JobStatusSchema(**j) for j in _scheduler.get_status()]
