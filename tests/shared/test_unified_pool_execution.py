"""
Unit and Integration Tests for Single Unified Capital Pool (₹10,000) & Single-Coin Asset Lock.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    BotName,
    MarketState,
    OppType,
    Position,
    PositionStatus,
    Priority,
    RiskLevel,
    Signal,
)
from execution.adapters import StrategyAdapterFactory
from execution.auto_trader import AutoTradeRouter
from execution.risk.capital_guard import CapitalGuard
from execution.risk.service import RiskService
from execution.service import TradingService
from execution.trading.precision_rules import validate_order_notional
from execution.trading.subaccount_manager import CoinDCXExecutionManager

# ── 1. Unified Capital Pool Sizing Tests ─────────────────────────────────────


def test_unified_capital_pool_configuration():
    cfg = AppConfig(
        total_capital_limit=10000.0,
        order_size_inr=200.0,
        max_concurrent_positions=10,
        enforce_single_coin_lock=True,
    )
    assert cfg.total_capital_limit == 10000.0
    assert cfg.order_size_inr == 200.0
    assert cfg.max_concurrent_positions == 10
    assert cfg.enforce_single_coin_lock is True


def test_order_sizing_and_precision_rounding():
    # Sizing for 4 strategy bots with ₹200 notional
    for bot in [BotName.STE, BotName.HDA, BotName.VCP, BotName.BBS]:
        adapter = StrategyAdapterFactory.get_adapter(bot)
        order = adapter.calculate_order(
            coin="SOL",
            pair="SOL/INR",
            approved_amount=200.0,
            current_price=12500.0,
            ai_adjustments={},
        )
        assert order["bot"] == bot
        assert order["coin"] == "SOL"
        assert order["pair"] == "SOL/INR"
        assert order["entry_price"] == 12500.0
        assert (
            order["qty"] == 0.02
        )  # 200 / 12500 = 0.016 -> rounded up to 0.02 step to satisfy ₹200 min invariant
        assert (
            order["amount"] == 250.0
        )  # 0.02 * 12500 = 250.0 (>= ₹200 minimum invariant)
        assert (
            validate_order_notional("SOL/INR", order["entry_price"], order["qty"])
            is True
        )


def test_min_notional_precision_rejection():
    # Order below ₹100 min notional must be rejected
    mgr = CoinDCXExecutionManager()
    client = mgr.get_client(BotName.BBS)
    # 1 DOGE @ ₹16.50 = ₹16.50 (< ₹100 minimum)
    res = client.place_order(pair="DOGE/INR", side="BUY", price=16.50, qty=1.0)
    assert res["success"] is False
    assert res["error"] == "ORDER_NOTIONAL_BELOW_MINIMUM"


# ── 2. Single-Coin Asset Deduplication & Fleet Lock Tests ─────────────────────


@pytest.mark.anyio
async def test_single_coin_fleet_lock_in_capital_guard():
    cfg = AppConfig(
        total_capital_limit=10000.0,
        max_concurrent_positions=10,
        enforce_single_coin_lock=True,
    )
    guard = CapitalGuard(cfg)

    # Existing active position on SOL by STE
    active_pos = [
        Position(
            id=str(uuid.uuid4()),
            bot=BotName.STE,
            coin="SOL",
            pair="SOL/INR",
            qty=0.01,
            entry_price=12500.0,
            entry_time=datetime.now(timezone.utc),
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
        )
    ]

    # 1. New trade on BTC by HDA -> ALLOWED
    d1 = guard.check_trade(
        bot=BotName.HDA,
        requested_amount=200.0,
        current_bot_deployed=0.0,
        total_deployed=125.0,
        current_bot_positions=0,
        active_positions=active_pos,
        current_coin="BTC",
    )
    assert d1.allowed is True
    assert d1.code == "ALLOWED"

    # 2. New trade on SOL by HDA -> BLOCKED by Single-Coin Lock
    d2 = guard.check_trade(
        bot=BotName.HDA,
        requested_amount=200.0,
        current_bot_deployed=0.0,
        total_deployed=125.0,
        current_bot_positions=0,
        active_positions=active_pos,
        current_coin="SOL",
    )
    assert d2.allowed is False
    assert d2.code == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"
    assert "already has an active open position" in d2.reason
    assert "Cross-strategy lock prevents opening in HDA" in d2.reason


@pytest.mark.anyio
async def test_cross_strategy_coin_lock_all_permutations():
    cfg = AppConfig(
        total_capital_limit=10000.0,
        max_concurrent_positions=10,
        enforce_single_coin_lock=True,
    )
    guard = CapitalGuard(cfg)

    # Active ETH position in BBS
    now = datetime.now(timezone.utc)
    active_pos = [
        Position(
            id=str(uuid.uuid4()),
            bot=BotName.BBS,
            coin="ETH",
            pair="ETH/INR",
            qty=0.01,
            entry_price=250000.0,
            entry_time=now,
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
        )
    ]

    # Test that STE, HDA, and VCP are all blocked from opening ETH
    for other_bot in [BotName.STE, BotName.HDA, BotName.VCP]:
        for notation in ["ETH", "ETH/INR", "B-ETH_INR", "B-ETH_USDT", "ETHUSDT"]:
            decision = guard.check_trade(
                bot=other_bot,
                requested_amount=200.0,
                current_bot_deployed=0.0,
                total_deployed=200.0,
                current_bot_positions=0,
                active_positions=active_pos,
                current_coin=notation,
            )
            assert decision.allowed is False, (
                f"Expected {notation} to be blocked for {other_bot.value}"
            )
            assert decision.code == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"
            assert "Cross-strategy lock prevents opening" in decision.reason


@pytest.mark.anyio
async def test_auto_trade_router_cross_strategy_position_rejection():
    bus = EventBus()
    now = datetime.now(timezone.utc)

    # Mock position repo with an active BTC position in STE
    class MockPositionRepo:
        async def get_open(self):
            return [
                Position(
                    id="mock-pos-1",
                    bot=BotName.STE,
                    coin="BTC",
                    pair="BTC/INR",
                    qty=0.001,
                    entry_price=8000000.0,
                    entry_time=now,
                    mode=BotMode.PAPER,
                    status=PositionStatus.OPEN,
                )
            ]

    router = AutoTradeRouter(
        bus=bus,
        dry_run=True,
        position_repo=MockPositionRepo(),
    )

    # Candidate signal for HDA on BTC -> should be rejected because STE already holds BTC
    signal_hda = {
        "id": "sig-btc-001",
        "coin": "BTC",
        "pair": "BTC/INR",
        "target_bot": "HDA",
        "price": 8000000.0,
        "trade_amount": 200.0,
    }
    result = await router.handle_signal(signal_hda)
    assert result["success"] is False
    assert result["error"] == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"
    assert "Cross-strategy lock prevents opening in HDA" in result["message"]


# ── 3. Fleet Capacity & Unified Pool Ceiling Tests ───────────────────────────


@pytest.mark.anyio
async def test_fleet_max_concurrent_positions_limit():
    cfg = AppConfig(
        total_capital_limit=10000.0,
        max_concurrent_positions=3,  # Set small cap for test
        enforce_single_coin_lock=True,
    )
    guard = CapitalGuard(cfg)

    now = datetime.now(timezone.utc)
    # 3 active positions already open
    active_positions = [
        Position(
            id="1",
            bot=BotName.STE,
            coin="BTC",
            pair="BTC/INR",
            qty=0.001,
            entry_price=8000000.0,
            entry_time=now,
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
        ),
        Position(
            id="2",
            bot=BotName.HDA,
            coin="ETH",
            pair="ETH/INR",
            qty=0.01,
            entry_price=250000.0,
            entry_time=now,
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
        ),
        Position(
            id="3",
            bot=BotName.VCP,
            coin="SOL",
            pair="SOL/INR",
            qty=0.01,
            entry_price=12500.0,
            entry_time=now,
            mode=BotMode.PAPER,
            status=PositionStatus.OPEN,
        ),
    ]

    # 4th trade on AVAX -> BLOCKED (capacity reached)
    d = guard.check_trade(
        bot=BotName.BBS,
        requested_amount=200.0,
        current_bot_deployed=0.0,
        total_deployed=650.0,
        current_bot_positions=0,
        active_positions=active_positions,
        current_coin="AVAX",
    )
    assert d.allowed is False
    assert d.code == "BLOCKED_MAX_FLEET_POSITIONS"


@pytest.mark.anyio
async def test_unified_capital_pool_ceiling_enforcement():
    cfg = AppConfig(
        total_capital_limit=10000.0,
        max_concurrent_positions=10,
    )
    guard = CapitalGuard(cfg)

    # ₹9,900 already deployed out of ₹10,000 pool
    d = guard.check_trade(
        bot=BotName.STE,
        requested_amount=200.0,
        current_bot_deployed=2000.0,
        total_deployed=9900.0,
        current_bot_positions=2,
        active_positions=[],
        current_coin="LINK",
    )
    assert d.allowed is False
    assert d.code == "BLOCKED_TOTAL_CAPITAL"
    assert "Total portfolio capital limit exceeded" in d.reason


# ── 4. End-to-End Simultaneous Execution Gating ──────────────────────────────


@pytest.mark.anyio
async def test_simultaneous_ste_and_hda_signal_deduplication(tmp_path):
    db_path = str(tmp_path / f"test_pool_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_path)
    await db.open()
    try:
        conn = db.connection
        sig_repo = SignalRepository(conn)
        pos_repo = PositionRepository(conn)
        trade_repo = TradeRepository(conn)
        event_repo = EventLogRepository(conn)
        bus = EventBus()

        cfg = AppConfig(
            total_capital_limit=10000.0,
            order_size_inr=200.0,
            max_concurrent_positions=10,
            enforce_single_coin_lock=True,
            v2_trading_enabled=True,
        )

        # Pre-populate signals
        now = datetime.now(timezone.utc)
        sig_ste = Signal(
            id="sig-ste-001",
            coin="SOL",
            pair="SOL/INR",
            market_state=MarketState.BREAKOUT,
            opportunity_type=OppType.MOMENTUM_TRADE,
            priority=Priority.HIGH,
            risk_level=RiskLevel.MEDIUM,
            score=90,
            confidence=85,
            coin_class="A",
            mtf_alignment=True,
            generated_at=now,
            expires_at=now,
        )
        await sig_repo.insert(sig_ste)

        sig_hda = Signal(
            id="sig-hda-002",
            coin="SOL",
            pair="SOL/INR",
            market_state=MarketState.BREAKOUT,
            opportunity_type=OppType.MOMENTUM_TRADE,
            priority=Priority.HIGH,
            risk_level=RiskLevel.MEDIUM,
            score=92,
            confidence=88,
            coin_class="A",
            mtf_alignment=True,
            generated_at=now,
            expires_at=now,
        )
        await sig_repo.insert(sig_hda)

        risk_svc = RiskService(bus, pos_repo, trade_repo, event_repo, cfg)
        exec_mgr = CoinDCXExecutionManager()
        trading_svc = TradingService(
            bus, pos_repo, trade_repo, event_repo, cfg, subaccount_manager=exec_mgr
        )

        await risk_svc.start()
        await trading_svc.start()

        # 1. Trigger STE on SOL -> gets APPROVED & EXECUTED
        payload_ste = {
            "signal_id": "sig-ste-001",
            "coin": "SOL",
            "pair": "SOL/INR",
            "bot": "STE",
            "price": 12500.0,
            "suggested_adjustments": {"size_multiplier": 1.0},
        }
        await risk_svc.on_signal_ai_confirmed(
            EventType.SIGNAL_AI_CONFIRMED, payload_ste
        )
        await asyncio.sleep(0.05)

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        assert open_positions[0].coin == "SOL"
        assert open_positions[0].bot == BotName.STE

        # 2. Trigger HDA on SOL simultaneously -> gets DENIED by single-coin lock
        denied_events = []

        async def on_trade_denied(ev, data):
            denied_events.append(data)

        bus.subscribe(EventType.TRADE_DENIED, on_trade_denied)

        payload_hda = {
            "signal_id": "sig-hda-002",
            "coin": "SOL",
            "pair": "SOL/INR",
            "bot": "HDA",
            "price": 12500.0,
            "suggested_adjustments": {"size_multiplier": 1.0},
        }
        await risk_svc.on_signal_ai_confirmed(
            EventType.SIGNAL_AI_CONFIRMED, payload_hda
        )
        await asyncio.sleep(0.05)

        assert len(denied_events) == 1
        assert denied_events[0]["code"] == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"
        assert denied_events[0]["coin"] == "SOL"

        # Still only 1 position open in database
        final_positions = await pos_repo.get_open()
        assert len(final_positions) == 1

        await risk_svc.stop()
        await trading_svc.stop()
    finally:
        await db.close()
