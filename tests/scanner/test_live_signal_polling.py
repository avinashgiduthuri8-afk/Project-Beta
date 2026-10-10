"""
Tests for Alpha-Live Scanner Signal Ingestion and Cross-Process Polling (MON-05).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.types import MarketState, OppType, Priority, RiskLevel, Signal
from live.execution.router import LiveExecutionRouter
from live.main import live_signal_payload


def test_live_signal_payload_from_entity():
    """Verify live_signal_payload correctly constructs a LIVE payload from a Signal object."""
    now = datetime.now(timezone.utc)
    sig = Signal(
        id="SIG-TEST-LIVE-1",
        coin="SOL",
        pair="SOL/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.MEDIUM,
        score=92,
        confidence=88,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now,
        source_bot="STE",
        raw_payload={
            "strategy": "STE",
            "entry_price": 12500.0,
            "take_profit": 13800.0,
            "stop_loss": 12000.0,
            "indicators": {"volume_24h": 500000.0},
            "signal_reason": "Strong breakout momentum",
        },
    )

    payload = live_signal_payload(sig)
    assert payload["id"] == "SIG-TEST-LIVE-1"
    assert payload["mode"] == "LIVE"
    assert payload["coin"] == "SOL"
    assert payload["pair"] == "SOL/INR"
    assert payload["entry_price"] == 12500.0
    assert payload["score"] == 92


@pytest.mark.asyncio
async def test_live_execution_router_accepts_live_polled_signal():
    """Verify LiveExecutionRouter accepts and processes a LIVE signal formatted from scanner DB."""
    bus = EventBus()
    auto_trader = MagicMock()
    auto_trader.dry_run = True
    auto_trader.handle_signal = AsyncMock(return_value={"success": True, "executed": True})

    router = LiveExecutionRouter(bus=bus, auto_trader=auto_trader)

    now = datetime.now(timezone.utc)
    sig = Signal(
        id="SIG-TEST-LIVE-2",
        coin="BTC",
        pair="BTC/INR",
        market_state=MarketState.BREAKOUT,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=95,
        confidence=90,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now,
        source_bot="STE",
        raw_payload={"price": 6000000.0},
    )

    payload = live_signal_payload(sig)
    result = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)

    assert result is not None
    assert result.get("success") is True
    assert auto_trader.handle_signal.call_count == 1
