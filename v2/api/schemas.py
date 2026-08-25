"""
V2 API Pydantic response schemas.

These are the wire types returned by /api/v2/* endpoints.
They are intentionally a superset of V1 /api/v1/* schemas so
clients can migrate incrementally.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Signal ────────────────────────────────────────────────────────────────────

class SignalSchema(BaseModel):
    id:               str
    coin:             str
    pair:             str
    market_state:     str
    opportunity_type: str
    priority:         str
    risk_level:       str
    score:            int
    confidence:       int
    coin_class:       Optional[str]
    mtf_alignment:    bool
    generated_at:     datetime
    expires_at:       datetime
    source_bot:       str = "scanner_v1"

    model_config = {"from_attributes": True}


# ── Scanner health ────────────────────────────────────────────────────────────

class ScannerHealthSchema(BaseModel):
    healthy:        bool
    poll_count:     int
    live_signals:   int
    last_poll_at:   Optional[str]
    last_error:     Optional[str]


# ── Scheduler job status ──────────────────────────────────────────────────────

class JobStatusSchema(BaseModel):
    name:               str
    enabled:            bool
    interval_s:         int
    run_count:          int
    error_count:        int
    consecutive_errors: int
    last_run_at:        Optional[str]
    last_duration_ms:   Optional[int]
    last_error:         Optional[str]


# ── System status ─────────────────────────────────────────────────────────────

class V2StatusSchema(BaseModel):
    version:         str = "2.1.0"
    status:          str = "ok"
    scanner_health:  ScannerHealthSchema
    scheduler_jobs:  list[JobStatusSchema]
    db_path:         str
    uptime_polls:    int
    live_signals:    int


# ── Generic success ───────────────────────────────────────────────────────────

class OkSchema(BaseModel):
    ok: bool = True
    detail: Optional[str] = None
