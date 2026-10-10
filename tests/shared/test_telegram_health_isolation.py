"""
Tests for Telegram /health component reporting in the isolated 3-service architecture:
- Alpha-Scanner (Brain)
- Alpha-Paper (Paper Execution)
- Alpha-Live (Live Execution)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from telegram.formatters import format_telegram_health
from telegram.telegram_interface import TelegramInteractiveInterface


class DummyTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.default_chat_id = "12345"

    async def send_message(self, text: str, target_chat_id: str | None = None, reply_markup: dict | None = None) -> dict:
        self.sent_messages.append({
            "text": text,
            "target_chat_id": target_chat_id,
            "reply_markup": reply_markup,
        })
        return {"ok": True}


class DummyRepo:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    @property
    def connection(self):
        if not self.healthy:
            return None
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        return mock_conn


@pytest.fixture
def bus() -> EventBus:
    b = EventBus()
    # Subscribe a dummy handler for SIGNAL_GENERATED
    async def dummy_handler(event_type: EventType, payload: dict) -> None:
        pass
    b.subscribe(EventType.SIGNAL_GENERATED, dummy_handler)
    return b


@pytest.fixture
def cfg() -> AppConfig:
    c = AppConfig()
    c.deployment_mode = "PAPER"
    c.alert_chat_id = "12345"
    return c


@pytest.mark.anyio
async def test_paper_execution_health_healthy(bus: EventBus, cfg: AppConfig):
    """Verify Paper execution service reports all components 🟢 and overall HEALTHY."""
    tg = DummyTelegramClient()
    pos_repo = DummyRepo(healthy=True)
    mock_router = MagicMock()
    mock_router.auto_trader = MagicMock()
    mock_router.auto_trader._subaccount_manager = MagicMock()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg,
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        execution_router=mock_router,
        mode_override="PAPER",
    )

    with patch.object(c2, "_check_scanner_health", return_value=True), \
         patch.object(c2, "_check_coindcx_health", new_callable=AsyncMock, return_value=True):
        data = await c2._compile_health_data()
        text = format_telegram_health(data)

    assert data["mode"] == "PAPER"
    assert data["overall"] == "healthy"
    assert data["components"]["scanner"] is True
    assert data["components"]["signal_intake"] is True
    assert data["components"]["risk"] is True
    assert data["components"]["execution"] is True
    assert data["components"]["database"] is True
    assert data["components"]["event_bus"] is True
    assert data["components"]["coindcx"] is True

    # Formatting assertions
    assert "PAPER EXECUTION SERVICE HEALTH" in text
    assert "Mode: PAPER" in text or "Mode:</b> <code>PAPER</code>" in text
    assert "Scanner Connection      🟢" in text
    assert "Signal Intake           🟢" in text
    assert "Risk Engine             🟢" in text
    assert "Paper Execution         🟢" in text
    assert "Paper Database          🟢" in text
    assert "EventBus                🟢" in text
    assert "CoinDCX Connectivity    🟢" in text
    assert "Overall Status:" in text and "HEALTHY" in text
    assert "PROJECT-ALPHA SERVICE HEALTH" in text
    assert "Paper Execution         🟢" in text

    # Ensure local absence of AI model does NOT appear in Paper execution health
    assert "AI 🔴" not in text
    assert "AI            🔴" not in text


@pytest.mark.anyio
async def test_live_execution_health_healthy(bus: EventBus):
    """Verify Live execution service reports all components 🟢 and overall HEALTHY."""
    cfg = AppConfig()
    cfg.deployment_mode = "LIVE"
    cfg.alert_chat_id = "12345"

    tg = DummyTelegramClient()
    pos_repo = DummyRepo(healthy=True)
    mock_router = MagicMock()
    mock_router.auto_trader = MagicMock()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg,
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        execution_router=mock_router,
        mode_override="LIVE",
    )

    with patch.object(c2, "_check_scanner_health", return_value=True), \
         patch.object(c2, "_check_coindcx_health", new_callable=AsyncMock, return_value=True):
        data = await c2._compile_health_data()
        text = format_telegram_health(data)

    assert data["mode"] == "LIVE"
    assert data["overall"] == "healthy"
    assert "LIVE EXECUTION SERVICE HEALTH" in text
    assert "Mode: LIVE" in text or "Mode:</b> <code>LIVE</code>" in text
    assert "Live Execution          🟢" in text
    assert "Live Database           🟢" in text
    assert "Overall Status:" in text and "HEALTHY" in text


@pytest.mark.anyio
async def test_live_execution_degraded_when_coindcx_down(bus: EventBus):
    """Verify Live execution service marks overall DEGRADED if CoinDCX auth fails."""
    cfg = AppConfig()
    cfg.deployment_mode = "LIVE"
    cfg.alert_chat_id = "12345"

    tg = DummyTelegramClient()
    pos_repo = DummyRepo(healthy=True)
    mock_router = MagicMock()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg,
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        execution_router=mock_router,
        mode_override="LIVE",
    )

    with patch.object(c2, "_check_scanner_health", return_value=True), \
         patch.object(c2, "_check_coindcx_health", new_callable=AsyncMock, return_value=False):
        data = await c2._compile_health_data()
        text = format_telegram_health(data)

    assert data["mode"] == "LIVE"
    assert data["components"]["coindcx"] is False
    assert data["overall"] == "degraded"
    assert "CoinDCX Connectivity    🔴" in text
    assert "Overall Status:" in text and "DEGRADED" in text


@pytest.mark.anyio
async def test_database_failure_degrades_service(bus: EventBus, cfg: AppConfig):
    """Verify that database connection loss reports 🔴 and degrades overall status."""
    tg = DummyTelegramClient()
    broken_repo = DummyRepo(healthy=False)
    mock_router = MagicMock()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg,
        bus=bus,
        config=cfg,
        position_repo=broken_repo,
        execution_router=mock_router,
        mode_override="PAPER",
    )

    with patch.object(c2, "_check_scanner_health", return_value=True), \
         patch.object(c2, "_check_coindcx_health", new_callable=AsyncMock, return_value=True):
        data = await c2._compile_health_data()
        text = format_telegram_health(data)

    assert data["components"]["database"] is False
    assert data["overall"] == "degraded"
    assert "Paper Database          🔴" in text
    assert "Overall Status:" in text and "DEGRADED" in text


@pytest.mark.anyio
async def test_signal_intake_failure_when_no_subscribers(cfg: AppConfig):
    """Verify Signal Intake reports 🔴 when EventBus has no SIGNAL_GENERATED listeners."""
    empty_bus = EventBus()
    tg = DummyTelegramClient()
    pos_repo = DummyRepo(healthy=True)
    mock_router = MagicMock()

    c2 = TelegramInteractiveInterface(
        telegram_client=tg,
        bus=empty_bus,
        config=cfg,
        position_repo=pos_repo,
        execution_router=mock_router,
        mode_override="PAPER",
    )

    with patch.object(c2, "_check_scanner_health", return_value=True), \
         patch.object(c2, "_check_coindcx_health", new_callable=AsyncMock, return_value=True):
        data = await c2._compile_health_data()
        text = format_telegram_health(data)

    assert data["components"]["signal_intake"] is False
    assert data["overall"] == "degraded"
    assert "Signal Intake           🔴" in text
    assert "Overall Status:" in text and "DEGRADED" in text


@pytest.mark.anyio
async def test_circuit_breaker_tripped_degrades_risk(bus: EventBus, cfg: AppConfig):
    """Verify tripped circuit breaker reports Risk Engine 🔴 and degrades health."""
    tg = DummyTelegramClient()
    pos_repo = DummyRepo(healthy=True)
    mock_risk = MagicMock()
    mock_risk.circuit_open = True

    c2 = TelegramInteractiveInterface(
        telegram_client=tg,
        bus=bus,
        config=cfg,
        position_repo=pos_repo,
        risk_service=mock_risk,
        execution_router=MagicMock(),
        mode_override="PAPER",
    )

    with patch.object(c2, "_check_scanner_health", return_value=True), \
         patch.object(c2, "_check_coindcx_health", new_callable=AsyncMock, return_value=True):
        data = await c2._compile_health_data()
        text = format_telegram_health(data)

    assert data["components"]["risk"] is False
    assert data["overall"] == "degraded"
    assert "Risk Engine             🔴" in text
    assert "Overall Status:" in text and "DEGRADED" in text
