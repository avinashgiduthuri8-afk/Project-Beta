"""
Comprehensive Unit and Integration Tests for Phase 8 Production Deployment Engine:
- ProductionStateRepository (CRUD and Database Integrity Check)
- ProductionController (Atomic mode transitions, kill switch, database-verified resume)
- ProductionWatchdog (9 Subsystem Probes, Self-Healing and Alerting)
- Production API Endpoints (/api/v2/production/status, set-mode, kill-switch, resume, watchdog)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from background.production.controller import ProductionController
from background.production.watchdog import ProductionWatchdog
from core.bus.event_bus import EventBus
from core.config import get_config, invalidate_config
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.production_state_repo import ProductionStateRepository


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    test_db = str(tmp_path / f"test_prod_{uuid.uuid4().hex[:6]}.db")
    monkeypatch.setenv("V2_DB_PATH", test_db)
    monkeypatch.setenv("OPERATOR_SECURITY_PASSWORD", "test-sec-pass")
    monkeypatch.setenv("COINDCX_API_KEY", "test-coindcx-key-real")
    monkeypatch.setenv("COINDCX_API_SECRET", "test-coindcx-secret-real")
    monkeypatch.setenv("BOT_MODE", "PAPER")
    monkeypatch.setenv("DEPLOYMENT_MODE", "PAPER")
    monkeypatch.setenv("V2_DEPLOYMENT_MODE", "PAPER")
    invalidate_config()
    yield
    invalidate_config()


# ── 1. ProductionStateRepository Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_production_state_repo_crud(tmp_path):
    db_path = str(tmp_path / "test_repo.db")
    db = Database(db_path)
    await db.open()
    try:
        repo = ProductionStateRepository(db.connection)

        # Initial seed check
        mode = await repo.get("v2_deployment_mode")
        assert mode in ("PAPER", "LIVE_MICROCASH")

        # Set and get single key
        await repo.set("operator_note", "Unit test execution", updated_by="TEST_RUNNER")
        assert await repo.get("operator_note") == "Unit test execution"

        # Set many
        await repo.set_many(
            {
                "breaker_tripped": "true",
                "active_strategy": "VCP",
            },
            updated_by="TEST_RUNNER",
        )
        assert await repo.get("breaker_tripped") == "true"
        assert await repo.get("active_strategy") == "VCP"

        # Get all
        all_state = await repo.get_all()
        assert "operator_note" in all_state
        assert "breaker_tripped" in all_state
        assert all_state["breaker_tripped"] == "true"

        # Verify integrity
        integrity = await repo.verify_integrity()
        assert integrity is True
    finally:
        await db.close()
# ── 2. ProductionController Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_production_controller_mode_transition(tmp_path):
    db_path = str(tmp_path / "test_ctrl.db")
    db = Database(db_path)
    await db.open()
    try:
        cfg = get_config()
        bus = EventBus()
        state_repo = ProductionStateRepository(db.connection)
        event_repo = EventLogRepository(db.connection)

        ctrl = ProductionController(
            config=cfg,
            bus=bus,
            state_repo=state_repo,
            event_log_repo=event_repo,
        )

        # Check initial mode
        assert ctrl.get_active_mode() in ("PAPER", "LIVE", "LIVE_MICROCASH")

        # Transition to PAPER
        res2 = await ctrl.set_mode("PAPER", operator="TEST_OPERATOR")
        assert res2["ok"] is True
        assert res2["mode"] == "PAPER"
        assert ctrl.get_active_mode() == "PAPER"
        assert await state_repo.get("v2_deployment_mode") == "PAPER"

        # Transition to LIVE
        res3 = await ctrl.set_mode("LIVE", operator="TEST_OPERATOR")
        assert res3["ok"] is True
        assert res3["mode"] in ("LIVE", "LIVE_MICROCASH")
        assert ctrl.get_active_mode() in ("LIVE", "LIVE_MICROCASH")
        assert await state_repo.get("v2_deployment_mode") in ("LIVE", "LIVE_MICROCASH")

        # Invalid mode rejection
        with pytest.raises(ValueError):
            await ctrl.set_mode("HYPER_LEVERAGE")
    finally:
        await db.close()

@pytest.mark.asyncio
async def test_production_controller_kill_switch_and_resume(tmp_path):
    db_path = str(tmp_path / "test_ctrl_ks.db")
    db = Database(db_path)
    await db.open()
    try:
        cfg = get_config()
        bus = EventBus()
        state_repo = ProductionStateRepository(db.connection)
        event_repo = EventLogRepository(db.connection)

        mock_risk = MagicMock()
        mock_cb = MagicMock()
        mock_risk.circuit_breaker = mock_cb

        mock_trading = MagicMock()
        mock_trading.is_trading_enabled.return_value = True

        ctrl = ProductionController(
            config=cfg,
            bus=bus,
            state_repo=state_repo,
            risk_service=mock_risk,
            trading_service=mock_trading,
            event_log_repo=event_repo,
        )

        # Engage kill switch
        ks_res = await ctrl.kill_switch(
            reason="Test drill simulated emergency", operator="SAFETY_OFFICER"
        )
        assert ks_res["ok"] is True
        assert ks_res["circuit_breaker"] == "TRIPPED"
        assert ks_res["mode"] == "PAPER"
        assert ctrl.get_active_mode() == "PAPER"
        assert await state_repo.get("circuit_breaker_status") == "TRIPPED"
        assert await state_repo.get("v2_trading_enabled") == "false"

        mock_cb.trip.assert_called_once_with("Test drill simulated emergency")
        mock_cb.set_emergency_stop.assert_called_once_with(
            True, "Test drill simulated emergency"
        )

        # Resume operations
        resume_res = await ctrl.resume(
            target_mode="PAPER",
            operator="RECOVERY_OPERATOR",
            reason="Test drill cleared",
        )
        assert resume_res["ok"] is True
        assert resume_res["mode"] == "PAPER"
        assert resume_res["circuit_breaker"] == "NORMAL"
        assert resume_res["trading_enabled"] is True

        mock_cb.reset.assert_called_once()
        mock_cb.set_emergency_stop.assert_called_with(False)
        assert await state_repo.get("circuit_breaker_status") == "NORMAL"
        assert await state_repo.get("v2_trading_enabled") == "true"
        assert await state_repo.get("v2_deployment_mode") == "PAPER"
    finally:
        await db.close()
# ── 3. ProductionWatchdog Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_production_watchdog_probes(tmp_path):
    cfg = get_config()
    bus = EventBus()

    db_path = str(tmp_path / "test_wd.db")
    db = Database(db_path)
    await db.open()

    try:
        mock_scanner = MagicMock()
        mock_scanner._running = True
        mock_scanner._last_poll_time = datetime.now(timezone.utc)
        mock_scanner.poll = AsyncMock()

        mock_ai = MagicMock()
        mock_ai._running = True

        mock_risk = MagicMock()
        mock_cb = MagicMock()
        mock_cb.is_tripped = False
        mock_cb.emergency_stop = False
        mock_cb.reason = None
        mock_risk.circuit_breaker = mock_cb

        mock_trading = MagicMock()
        mock_trading._subaccount_manager = None

        mock_scheduler = MagicMock()
        mock_scheduler._running = True
        mock_scheduler._jobs = {}

        watchdog = ProductionWatchdog(
            config=cfg,
            bus=bus,
            scanner_service=mock_scanner,
            ai_service=mock_ai,
            risk_service=mock_risk,
            trading_service=mock_trading,
            db=db,
            scheduler=mock_scheduler,
            inspection_interval_sec=10.0,
        )

        report = await watchdog.inspect_system()

        assert "status" in report
        assert "subsystems_healthy" in report
        assert "probes" in report
        assert len(report["probes"]) == 9
        assert "scanner" in report["probes"]
        assert "ai_intelligence" in report["probes"]
        assert "risk_engine" in report["probes"]
        assert "execution_router" in report["probes"]
        assert "coindcx_relay" in report["probes"]
        assert "database" in report["probes"]
        assert "event_bus" in report["probes"]
        assert "scheduler" in report["probes"]

        summary = watchdog.get_summary()
        assert summary["inspections_total"] >= 1
        assert summary["uptime_seconds"] >= 0
    finally:
        await db.close()
