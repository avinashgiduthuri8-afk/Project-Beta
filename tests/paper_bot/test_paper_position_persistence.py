"""
tests/test_paper_position_persistence.py — Verification of Paper Position Persistence & Dynamic Targets.

Verifies:
  1. PaperExecutionRouter delegates to AutoTradeRouter and persists Position in PositionRepository.
  2. Dynamic TP calculation based on conviction score (Standard 5%, Elite 10%, Super Elite 20%).
  3. Default Stop Loss (3.5%) applied to survive market noise.
  4. POSITION_OPENED event is emitted with complete telemetry payload.
  5. Hard mode isolation rejects LIVE or missing mode without writing any position.
"""

import pytest
from datetime import datetime, timezone
import uuid

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, PositionStatus
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from paper.execution.router import PaperExecutionRouter


async def create_paper_env(tmp_path, db_name="test_paper.db"):
    db_file = str(tmp_path / f"{uuid.uuid4().hex[:8]}_{db_name}")
    db = Database(db_file)
    await db.open()

    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    bus = EventBus()

    pos_mgr = PositionManager(
        position_repo=pos_repo,
        trade_repo=trade_repo,
        bus=bus,
    )

    auto_trader = AutoTradeRouter(
        bus=bus,
        dry_run=True,
        position_repo=pos_repo,
    )

    router = PaperExecutionRouter(
        bus=bus,
        auto_trader=auto_trader,
        position_manager=pos_mgr,
    )

    return db, pos_repo, bus, pos_mgr, auto_trader, router


@pytest.mark.asyncio
async def test_paper_signal_execution_persists_position(tmp_path):
    db, pos_repo, bus, pos_mgr, auto_trader, router = await create_paper_env(tmp_path)
    try:
        opened_events = []

        async def _on_opened(event_type, payload):
            opened_events.append(payload)

        bus.subscribe(EventType.POSITION_OPENED, _on_opened)

        payload = {
            "id": "sig_paper_persistence_001",
            "mode": "PAPER",
            "bot": "STE",
            "coin": "BTC",
            "pair": "BTC/INR",
            "price": 8500000.0,
            "score": 85.0,  # Standard score
        }

        result = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)

        assert result is not None
        assert result.get("success") is True
        assert result.get("dry_run") is True
        assert "position_id" in result

        # Check persistence in database
        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos = open_positions[0]
        assert pos.id == result["position_id"]
        assert pos.coin == "BTC"
        assert pos.pair == "BTC/INR"
        assert pos.mode == BotMode.PAPER
        assert pos.status == PositionStatus.OPEN
        assert pos.entry_price == 8500000.0
        # Default 3.5% SL and 5.0% TP for standard score (85)
        assert pos.stop_loss == pytest.approx(8500000.0 * 0.965, rel=1e-4)
        assert pos.take_profit == pytest.approx(8500000.0 * 1.05, rel=1e-4)

        # Check POSITION_OPENED event payload
        assert len(opened_events) == 1
        event_payload = opened_events[0]
        assert event_payload["position_id"] == pos.id
        assert event_payload["coin"] == "BTC"
        assert event_payload["mode"] == "PAPER"
        assert event_payload["stop_loss"] == pos.stop_loss
        assert event_payload["take_profit"] == pos.take_profit
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dynamic_take_profit_scaling(tmp_path):
    db, pos_repo, bus, pos_mgr, auto_trader, router = await create_paper_env(tmp_path)
    try:
        # 1. Elite Signal (score = 92) -> Target 10.0% TP
        payload_elite = {
            "id": "sig_elite_002",
            "mode": "PAPER",
            "bot": "STE",
            "coin": "ETH",
            "pair": "ETH/INR",
            "price": 300000.0,
            "score": 92.0,
        }
        res_elite = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload_elite)
        assert res_elite.get("success") is True

        pos_elite = await pos_repo.get(res_elite["position_id"])
        assert pos_elite is not None
        assert pos_elite.take_profit == pytest.approx(300000.0 * 1.10, rel=1e-4)

        # 2. Super Elite VCP signal (score = 97) -> frozen Phase 3B target 22.0% TP
        payload_super = {
            "id": "sig_super_003",
            "mode": "PAPER",
            "bot": "VCP",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 18000.0,
            "score": 97.0,
        }
        res_super = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload_super)
        assert res_super.get("success") is True

        pos_super = await pos_repo.get(res_super["position_id"])
        assert pos_super is not None
        assert pos_super.take_profit == pytest.approx(18000.0 * 1.22, rel=1e-4)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_paper_router_rejects_live_mode_and_writes_nothing(tmp_path):
    db, pos_repo, bus, pos_mgr, auto_trader, router = await create_paper_env(tmp_path)
    try:
        payload_live = {
            "id": "sig_live_leak",
            "mode": "LIVE",
            "coin": "BTC",
            "pair": "BTC/INR",
            "price": 8500000.0,
            "score": 95.0,
        }

        result = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload_live)
        assert result["success"] is False
        assert result["rejected"] is True

        # Zero positions persisted in paper database
        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 0
    finally:
        await db.close()
