"""
PROJECT-ALPHA — Architecture Restructure & Three-Service Isolation Test.

Verifies:
  1. Service process configuration isolation: Scanner vs Paper vs Live.
  2. Database isolation: paper/data/alpha_paper.db vs live/data/alpha_live.db.
  3. Signal mode tag preservation (mode = PAPER, mode = LIVE).
  4. Paper router rejects mode != PAPER.
  5. Live router rejects mode != LIVE.
  6. Unknown/missing/None mode is rejected by both routers (Safety Requirement #8).
"""

import pytest
from core.bus.event_bus import EventBus
from core.types import BotMode, Signal, MarketState, OppType, Priority, RiskLevel
from paper.config.settings import get_paper_config
from paper.execution.router import PaperExecutionRouter
from live.config.settings import get_live_config
from live.execution.router import LiveExecutionRouter
from scanner.config.settings import get_scanner_config
from scanner.signals.dispatcher import SignalDispatcher
from shared.mode_guard import ModeIsolationError, validate_execution_mode
from datetime import datetime, timezone


def test_service_configurations():
    s_cfg = get_scanner_config()
    p_cfg = get_paper_config()
    l_cfg = get_live_config()

    assert s_cfg.mode == "SCANNER"
    assert s_cfg.db_path == "scanner/data/alpha_scanner.db"

    assert p_cfg.mode == "PAPER"
    assert p_cfg.db_path == "paper/data/alpha_paper.db"

    assert l_cfg.mode == "LIVE"
    assert l_cfg.db_path == "live/data/alpha_live.db"
    assert l_cfg.trading_enabled is False  # Safety default


def test_mode_guard_validation():
    # Valid PAPER mode
    payload_paper = {"id": "sig_1", "mode": "PAPER"}
    assert validate_execution_mode(payload_paper, expected_mode="PAPER") == "PAPER"

    # Valid LIVE mode
    payload_live = {"id": "sig_2", "mode": "LIVE"}
    assert validate_execution_mode(payload_live, expected_mode="LIVE") == "LIVE"

    # Paper component rejecting LIVE payload
    with pytest.raises(ModeIsolationError, match="Mode mismatch"):
        validate_execution_mode(payload_live, expected_mode="PAPER", component="Paper")

    # Live component rejecting PAPER payload
    with pytest.raises(ModeIsolationError, match="Mode mismatch"):
        validate_execution_mode(payload_paper, expected_mode="LIVE", component="Live")

    # Missing/None mode rejection
    payload_none = {"id": "sig_3", "mode": None}
    with pytest.raises(ModeIsolationError, match="missing/None/invalid"):
        validate_execution_mode(payload_none, expected_mode="PAPER")

    with pytest.raises(ModeIsolationError, match="missing/None/invalid"):
        validate_execution_mode(payload_none, expected_mode="LIVE")


@pytest.mark.asyncio
async def test_signal_dispatcher_cloning():
    dispatcher = SignalDispatcher(bus=None)
    now = datetime.now(timezone.utc)
    sig = Signal(
        id="sig_test_100",
        coin="BTC",
        pair="BTC/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.ELITE,
        risk_level=RiskLevel.LOW,
        score=95,
        confidence=90,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now,
    )

    result = await dispatcher.dispatch(sig)
    assert "paper" in result
    assert "live" in result


from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_paper_and_live_router_rejection():
    bus = EventBus()
    mock_auto_trader = MagicMock()
    mock_auto_trader.dry_run = True

    paper_router = PaperExecutionRouter(bus=bus, auto_trader=mock_auto_trader)
    live_router = LiveExecutionRouter(bus=bus, auto_trader=mock_auto_trader)

    # Paper router receiving LIVE signal -> MUST REJECT
    live_payload = {"id": "sig_live_1", "mode": "LIVE", "coin": "BTC"}
    paper_res = await paper_router.handle_signal_event(None, live_payload)
    assert paper_res["success"] is False
    assert paper_res["rejected"] is True
    assert "Mode mismatch" in paper_res["reason"]

    # Live router receiving PAPER signal -> MUST REJECT
    paper_payload = {"id": "sig_paper_1", "mode": "PAPER", "coin": "BTC"}
    live_res = await live_router.handle_signal_event(None, paper_payload)
    assert live_res["success"] is False
    assert live_res["rejected"] is True
    assert "Mode mismatch" in live_res["reason"]

    # Invalid mode (None) -> BOTH REJECT
    invalid_payload = {"id": "sig_bad", "mode": None}
    paper_bad_res = await paper_router.handle_signal_event(None, invalid_payload)
    live_bad_res = await live_router.handle_signal_event(None, invalid_payload)

    assert paper_bad_res["rejected"] is True
    assert live_bad_res["rejected"] is True
