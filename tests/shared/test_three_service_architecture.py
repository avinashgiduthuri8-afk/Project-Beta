"""The approved Beta v1 fleet is Scanner -> Paper -> Learner."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.bus.event_bus import EventBus
from core.types import BotMode, MarketState, OppType, Priority, RiskLevel, Signal
from learner.config import get_learner_config
from paper.config.settings import get_paper_config
from paper.execution.router import PaperExecutionRouter
from scanner.config.settings import get_scanner_config
from scanner.signals.dispatcher import SignalDispatcher
from shared.mode_guard import ModeIsolationError, validate_execution_mode


def test_service_order_and_shared_database():
    configs = (get_scanner_config(), get_paper_config(), get_learner_config())

    assert tuple(config.mode for config in configs) == ("SCANNER", "PAPER", "LEARNER")
    assert tuple(config.db_path for config in configs) == (
        "data/project_beta.db",
        "data/project_beta.db",
        "data/project_beta.db",
    )
    assert configs[1].scanner_db_path == configs[0].db_path
    assert configs[2].scanner_db_path == configs[0].db_path
    assert configs[2].paper_db_path == configs[1].db_path


def test_mode_guard_accepts_only_paper_at_the_v1_execution_boundary():
    assert validate_execution_mode({"mode": "PAPER"}, expected_mode="PAPER") == "PAPER"
    with pytest.raises(ModeIsolationError, match="Mode mismatch"):
        validate_execution_mode({"mode": "LIVE"}, expected_mode="PAPER")
    with pytest.raises(ModeIsolationError, match="missing/None/invalid"):
        validate_execution_mode({"mode": None}, expected_mode="PAPER")


@pytest.mark.asyncio
async def test_signal_dispatcher_targets_paper_only():
    now = datetime.now(timezone.utc)
    signal = Signal(
        id="sig_reliance_100",
        coin="RELIANCE",
        pair="NSE:RELIANCE",
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

    result = await SignalDispatcher(bus=None).dispatch(signal)
    assert result == {"paper": "sig_reliance_100_PAPER"}


@pytest.mark.asyncio
async def test_paper_router_rejects_non_paper_payloads():
    auto_trader = MagicMock(dry_run=True)
    router = PaperExecutionRouter(bus=EventBus(), auto_trader=auto_trader)

    result = await router.handle_signal_event(
        None, {"id": "sig_live_1", "mode": "LIVE", "symbol": "NSE:RELIANCE"}
    )
    assert result["rejected"] is True
    assert "Mode mismatch" in result["reason"]
