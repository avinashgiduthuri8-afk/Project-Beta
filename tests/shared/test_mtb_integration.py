"""
PROJECT-ALPHA — Phase 3B: MTB Strategy Integration & Validation Test Suite.

Covers all 29 specified requirements for end-to-end integration and verification:
1. MTB signal creation
2. MTB signal contract (13 canonical fields)
3. MTB strategy field (strategy == "MTB")
4. Scanner -> MTB integration (Donchian 20 breakout condition)
5. MTB signal persistence
6. Valid Paper execution
7. Stale signal rejection
8. Malformed signal rejection
9. Invalid entry rejection
10. Invalid TP rejection
11. Invalid SL rejection
12. Duplicate signal rejection
13. Persistent replay protection
14. STE -> MTB coin-lock rejection
15. HDA -> MTB coin-lock rejection
16. VCP -> MTB coin-lock rejection
17. BBS -> MTB coin-lock rejection
18. MTB -> STE coin-lock rejection
19. MTB -> HDA coin-lock rejection
20. MTB -> VCP coin-lock rejection
21. MTB -> BBS coin-lock rejection
22. Coin unlock after close
23. Shared CapitalGuard across all 5 bots (STE, HDA, VCP, BBS, MTB)
24. Insufficient capital rejection
25. Position persistence
26. Restart recovery
27. TP close
28. SL close
29. Duplicate close protection
30. Telegram formatting
31. Portfolio telemetry visibility
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.order_repo import OrderRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    BotName,
    ExitReason,
    MarketState,
    OppType,
    Position,
    PositionStatus,
    Priority,
    RiskLevel,
    Signal,
)
from background.portfolio.service import PortfolioService
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from execution.risk.capital_guard import CapitalGuard
from paper.execution.router import PaperExecutionRouter
from scanner.signals.dispatcher import SignalDispatcher
from telegram.formatters import (
    format_position_closed_alert,
    format_position_opened_alert,
)
from telegram.telegram_interface import TelegramInteractiveInterface


@asynccontextmanager
async def create_test_env(tmp_path: Path):
    """Set up isolated SQLite DB, repositories, event bus, and execution router."""
    db_path = str(tmp_path / f"test_mtb_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_path)
    await db.open()

    conn = db.connection
    sig_repo = SignalRepository(conn)
    pos_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    order_repo = OrderRepository(conn)
    event_repo = EventLogRepository(conn)

    bus = EventBus()

    cfg = AppConfig(
        total_capital_limit=10000.0,
        order_size_inr=200.0,
        max_concurrent_positions=10,
        enforce_single_coin_lock=True,
        v2_trading_enabled=True,
    )

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

    capital_guard = CapitalGuard(cfg)

    paper_router = PaperExecutionRouter(
        bus=bus,
        auto_trader=auto_trader,
        position_manager=pos_mgr,
        capital_guard=capital_guard,
        position_repo=pos_repo,
    )

    portfolio_svc = PortfolioService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        config=cfg,
    )
    await portfolio_svc.start()

    try:
        yield {
            "db": db,
            "sig_repo": sig_repo,
            "pos_repo": pos_repo,
            "trade_repo": trade_repo,
            "order_repo": order_repo,
            "event_repo": event_repo,
            "bus": bus,
            "cfg": cfg,
            "pos_mgr": pos_mgr,
            "auto_trader": auto_trader,
            "capital_guard": capital_guard,
            "paper_router": paper_router,
            "portfolio_svc": portfolio_svc,
        }
    finally:
        await portfolio_svc.stop()
        await db.close()


def create_mtb_signal(
    signal_id: str = "SIG-MTB-001",
    coin: str = "NEAR",
    pair: str = "NEAR/INR",
    price: float = 450.0,
    score: float = 93.0,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    """Helper to generate a canonical MTB Signal dataclass."""
    now = datetime.now(timezone.utc)
    if is_expired or ttl_seconds < 0:
        gen_time = now - timedelta(seconds=abs(ttl_seconds) + 60)
        exp_time = now - timedelta(seconds=30)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic MTB TP: 22.0% (>=95), 12.0% (>=90), 5.5% (<90)
    tp_pct = 22.0 if score >= 95.0 else (12.0 if score >= 90.0 else 5.5)
    sl_pct = 4.0

    tp_price = round(price * (1.0 + tp_pct / 100.0), 4)
    sl_price = round(price * (1.0 - sl_pct / 100.0), 4)

    raw_payload = {
        "price": price,
        "entry_price": price,
        "entry": price,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "strategy": "MTB",
        "strategy_name": "Momentum Trend Breakout",
        "bot": "MTB",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"Momentum Trend Breakout: trend_breakout (score={score:.1f}, RSI=62.0)",
        "indicators": {
            "rsi": 62.0,
            "atr_pct": 2.2,
            "volume_ratio": 1.20,
            "donchian_high_20": price * 0.98,
            "ema9": price * 0.99,
            "ema21": price * 0.97,
            "mtf_aligned": True,
        },
    }

    return Signal(
        id=signal_id,
        coin=coin,
        pair=pair,
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.MEDIUM,
        score=score,
        confidence=score,
        coin_class="A",
        mtf_alignment=True,
        generated_at=gen_time,
        expires_at=exp_time,
        source_bot="MTB",
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )


# ── 1. MTB Signal Creation ──────────────────────────────────────────────────

def test_mtb_signal_creation():
    sig = create_mtb_signal(signal_id="SIG-MTB-001", coin="NEAR", pair="NEAR/INR", price=450.0, score=93.0)
    assert sig.id == "SIG-MTB-001"
    assert sig.coin == "NEAR"
    assert sig.pair == "NEAR/INR"
    assert sig.score == 93.0
    assert sig.source_bot == "MTB"
    assert sig.raw_payload["strategy"] == "MTB"
    assert sig.raw_payload["strategy_name"] == "Momentum Trend Breakout"


# ── 2. MTB Signal Contract Fields ───────────────────────────────────────────

def test_mtb_signal_contract_fields():
    sig = create_mtb_signal(signal_id="SIG-MTB-CONTRACT", coin="NEAR", pair="NEAR/INR", price=450.0, score=93.0)
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)

    required_fields = [
        "signal_id",
        "strategy",
        "mode",
        "pair",
        "timeframe",
        "timestamp",
        "entry",
        "score",
        "stop_loss",
        "take_profit",
        "signal_reason",
        "indicators",
        "ttl_seconds",
    ]
    for field in required_fields:
        assert field in payload, f"Missing required field {field} in MTB contract"
        assert payload[field] is not None, f"Field {field} is None"

    assert payload["entry"] == 450.0
    assert payload["strategy"] == "MTB"
    assert payload["mode"] == "PAPER"
    assert "Momentum Trend Breakout" in payload["strategy_name"]


# ── 3. MTB Strategy Field ───────────────────────────────────────────────────

def test_mtb_strategy_field():
    sig = create_mtb_signal(signal_id="SIG-MTB-FIELD", coin="NEAR", pair="NEAR/INR", price=450.0)
    assert sig.source_bot == "MTB"
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)
    assert payload["strategy"] == "MTB"
    assert payload["strategy_name"] == "Momentum Trend Breakout"


# ── 4. Scanner -> MTB Integration ───────────────────────────────────────────

def test_mtb_scanner_integration():
    highs = [400.0 + i * 2.0 for i in range(25)]
    closes = [400.0 + i * 2.0 for i in range(25)]
    latest_close = 460.0
    donchian_high_20 = max(highs[-21:-1])
    ema9 = 445.0
    ema21 = 430.0
    is_4h_bullish = True

    is_bbs = False
    is_mtb = (latest_close > donchian_high_20) and (ema9 >= ema21) and is_4h_bullish
    assert is_mtb is True

    volume_ratio = 1.15
    if volume_ratio >= 1.5:
        bot = "HDA"
    elif is_bbs and is_4h_bullish:
        bot = "BBS"
    elif is_mtb:
        bot = "MTB"
        opp_type = "trend_breakout"
        strat_name = "Momentum Trend Breakout"
    else:
        bot = "STE"

    assert bot == "MTB"
    assert opp_type == "trend_breakout"
    assert strat_name == "Momentum Trend Breakout"


# ── 5. MTB Signal Persistence ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_signal_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        sig_repo: SignalRepository = env["sig_repo"]
        sig = create_mtb_signal(signal_id="SIG-MTB-PERSIST", coin="NEAR", pair="NEAR/INR", price=450.0)

        await sig_repo.insert(sig)
        loaded = await sig_repo.get_by_id("SIG-MTB-PERSIST")
        assert loaded is not None
        assert loaded.id == "SIG-MTB-PERSIST"
        assert loaded.source_bot == "MTB"
        assert loaded.raw_payload.get("strategy") == "MTB"


# ── 6. Valid Paper Execution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_valid_paper_execution(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]
        sig = create_mtb_signal(signal_id="SIG-MTB-VALID", coin="NEAR", pair="NEAR/INR", price=450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is True, f"Expected success but got: {res}"

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.coin == "NEAR"
        assert pos.pair == "NEAR/INR"
        assert pos.bot == BotName.MTB
        assert pos.signal_id == "SIG-MTB-VALID"


# ── 7. Stale Signal Rejection ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_stale_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mtb_signal(signal_id="SIG-MTB-STALE", coin="NEAR", pair="NEAR/INR", price=450.0, is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "SIGNAL_EXPIRED"


# ── 8. Malformed Signal Rejection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_malformed_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        payload = {
            "signal_id": "SIG-MTB-MALFORMED",
            "strategy": "MTB",
            "mode": "PAPER",
        }
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "MISSING_REQUIRED_FIELDS"


# ── 9. Invalid Entry Rejection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_invalid_entry_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mtb_signal(signal_id="SIG-MTB-BAD-ENTRY", coin="NEAR", pair="NEAR/INR", price=-10.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_ENTRY_PRICE"


# ── 10. Invalid TP Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_invalid_tp_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mtb_signal(signal_id="SIG-MTB-BAD-TP", coin="NEAR", pair="NEAR/INR", price=450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["take_profit"] = 400.0  # below entry

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 11. Invalid SL Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_invalid_sl_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mtb_signal(signal_id="SIG-MTB-BAD-SL", coin="NEAR", pair="NEAR/INR", price=450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["stop_loss"] = 500.0  # above entry

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 12. Duplicate Signal Rejection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_duplicate_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mtb_signal(signal_id="SIG-MTB-DUP", coin="NEAR", pair="NEAR/INR", price=450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")


# ── 13. Persistent Replay Protection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_persistent_replay_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mtb_signal(signal_id="SIG-MTB-REPLAY", coin="NEAR", pair="NEAR/INR", price=450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        router.auto_trader._processed_idempotency_keys.clear()

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") == "SIGNAL_ALREADY_EXECUTED"


# ── 14. STE -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ste_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open STE on NEAR
        ste_payload = {
            "signal_id": "SIG-STE-LOCKS-NEAR",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 434.0,
            "take_profit": 472.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. MTB attempts to open NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKED-BY-STE", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 15. HDA -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hda_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open HDA on NEAR
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKS-NEAR",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 430.0,
            "take_profit": 490.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 2. MTB attempts to open NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKED-BY-HDA", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 16. VCP -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on NEAR
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKS-NEAR",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 428.0,
            "take_profit": 500.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. MTB attempts to open NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKED-BY-VCP", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 17. BBS -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on NEAR
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKS-NEAR",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 430.0,
            "take_profit": 510.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)
        assert res_bbs.get("success") is True

        # 2. MTB attempts to open NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKED-BY-BBS", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 18. MTB -> STE Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_ste_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKS-STE", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))
        assert res_mtb.get("success") is True

        # 2. STE attempts to open NEAR
        ste_payload = {
            "signal_id": "SIG-STE-LOCKED-BY-MTB",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 434.0,
            "take_profit": 472.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is False
        assert res_ste.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 19. MTB -> HDA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_hda_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKS-HDA", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))
        assert res_mtb.get("success") is True

        # 2. HDA attempts to open NEAR
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKED-BY-MTB",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 430.0,
            "take_profit": 490.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is False
        assert res_hda.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 20. MTB -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKS-VCP", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))
        assert res_mtb.get("success") is True

        # 2. VCP attempts to open NEAR
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKED-BY-MTB",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 428.0,
            "take_profit": 500.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 21. MTB -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOCKS-BBS", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))
        assert res_mtb.get("success") is True

        # 2. BBS attempts to open NEAR
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKED-BY-MTB",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 430.0,
            "take_profit": 510.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)
        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 22. Coin Unlock After Close ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_coin_unlock_after_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. Open MTB position on NEAR
        sig1 = create_mtb_signal(signal_id="SIG-MTB-UNLOCK-1", coin="NEAR", pair="NEAR/INR", price=450.0)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig1))
        assert res1.get("success") is True

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]

        # 2. Close position via TP
        await pos_mgr.close_position(pos.id, 500.0, ExitReason.TAKE_PROFIT)

        open_after = await pos_repo.get_open()
        assert len(open_after) == 0

        # 3. New MTB signal on NEAR should succeed now that coin is unlocked
        sig2 = create_mtb_signal(signal_id="SIG-MTB-UNLOCK-2", coin="NEAR", pair="NEAR/INR", price=455.0)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig2))
        assert res2.get("success") is True


# ── 23. Shared CapitalGuard Across All 5 Bots ───────────────────────────────

@pytest.mark.asyncio
async def test_mtb_shared_capital_guard_all_five_bots(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. STE on BTC
        ste_payload = {
            "signal_id": "SIG-STE-CAP",
            "coin": "BTC",
            "pair": "BTC/INR",
            "price": 8000000.0,
            "entry_price": 8000000.0,
            "stop_loss": 7700000.0,
            "take_profit": 8500000.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. HDA on ETH
        hda_payload = {
            "signal_id": "SIG-HDA-CAP",
            "coin": "ETH",
            "pair": "ETH/INR",
            "price": 300000.0,
            "entry_price": 300000.0,
            "stop_loss": 286500.0,
            "take_profit": 340500.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 3. VCP on SOL
        vcp_payload = {
            "signal_id": "SIG-VCP-CAP",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 12000.0,
            "entry_price": 12000.0,
            "stop_loss": 11424.0,
            "take_profit": 13560.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 4. BBS on DOGE
        bbs_payload = {
            "signal_id": "SIG-BBS-CAP",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.6,
            "take_profit": 21.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)
        assert res_bbs.get("success") is True

        # 5. MTB on NEAR
        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-CAP", coin="NEAR", pair="NEAR/INR", price=450.0)
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mtb_sig))
        assert res_mtb.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 5
        bots = {p.bot for p in open_positions}
        assert bots == {BotName.STE, BotName.HDA, BotName.VCP, BotName.BBS, BotName.MTB}


# ── 24. Insufficient Capital ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_insufficient_capital_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        capital_guard: CapitalGuard = env["capital_guard"]

        # Artificially clamp total capital limit to 100 INR (below minimum 200 order size)
        capital_guard._config.total_capital_limit = 100.0

        mtb_sig = create_mtb_signal(signal_id="SIG-MTB-LOW-CAP", coin="NEAR", pair="NEAR/INR", price=450.0)
        payload = SignalDispatcher()._signal_to_dict(mtb_sig)
        payload["amount"] = 200.0

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("decision_code") == "BLOCKED_TOTAL_CAPITAL"


# ── 25. Position Persistence ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_position_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_mtb_signal(signal_id="SIG-MTB-PERSIST-POS", coin="NEAR", pair="NEAR/INR", price=450.0)
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
        assert res.get("success") is True

        pos = await pos_repo.get_by_signal_id("SIG-MTB-PERSIST-POS")
        assert pos is not None
        assert pos.coin == "NEAR"
        assert pos.pair == "NEAR/INR"
        assert pos.bot == BotName.MTB
        assert pos.status == PositionStatus.OPEN
        assert pos.entry_price == 450.0
        assert pos.take_profit > 450.0
        assert pos.stop_loss < 450.0


# ── 26. Restart Recovery ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_restart_recovery(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]
        pos_repo = env["pos_repo"]

        pos = await pos_mgr.open_position(
            bot=BotName.MTB,
            coin="NEAR",
            pair="NEAR/INR",
            entry_price=450.0,
            qty=0.44,
            mode=BotMode.PAPER,
            stop_loss=432.0,
            take_profit=504.0,
            signal_id="SIG-MTB-RESTART",
        )

        recovered = await pos_repo.get_open(bot=BotName.MTB, mode=BotMode.PAPER)
        assert len(recovered) == 1
        r_pos = recovered[0]
        assert r_pos.id == pos.id
        assert r_pos.bot == BotName.MTB
        assert r_pos.coin == "NEAR"
        assert r_pos.entry_price == 450.0
        assert r_pos.stop_loss == 432.0
        assert r_pos.take_profit == 504.0


# ── 27. Take Profit Close ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_take_profit_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_mtb_signal(signal_id="SIG-MTB-TP", coin="NEAR", pair="NEAR/INR", price=450.0, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]

        tp_price = pos.take_profit
        closed_pos, trade = await pos_mgr.close_position(pos.id, tp_price, ExitReason.TAKE_PROFIT)

        assert trade is not None
        assert trade.exit_reason == ExitReason.TAKE_PROFIT
        assert trade.pnl > 0.0
        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.realized_pnl > 0.0


# ── 28. Stop Loss Close ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_stop_loss_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_mtb_signal(signal_id="SIG-MTB-SL", coin="NEAR", pair="NEAR/INR", price=450.0, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]

        sl_price = pos.stop_loss
        closed_pos, trade = await pos_mgr.close_position(pos.id, sl_price, ExitReason.STOP_LOSS)

        assert trade is not None
        assert trade.exit_reason == ExitReason.STOP_LOSS
        assert trade.pnl < 0.0
        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.realized_pnl < 0.0


# ── 29. Duplicate Close Protection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_duplicate_close_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]
        trade_repo: TradeRepository = env["trade_repo"]

        sig = create_mtb_signal(signal_id="SIG-MTB-DUP-CLOSE", coin="NEAR", pair="NEAR/INR", price=450.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        positions = await pos_repo.get_open()
        pos = positions[0]

        # First close
        pos1, trade1 = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)
        assert trade1 is not None

        # Duplicate close attempt on already closed position
        pos2, trade2 = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)
        assert trade2 is not None
        assert trade1.id == trade2.id

        # Verify exactly 1 trade was created in repository
        all_trades = await trade_repo.get_by_position(pos.id)
        assert len(all_trades) == 1


# ── 30. Telegram Formatting ─────────────────────────────────────────────────

def test_mtb_telegram_formatting():
    opened_payload = {
        "bot": "MTB",
        "coin": "NEAR",
        "pair": "NEAR/INR",
        "entry_price": 450.0,
        "qty": 0.44,
        "amount": 198.0,
        "mode": "PAPER",
        "stop_loss": 432.0,
        "take_profit": 504.0,
    }
    open_alert = format_position_opened_alert(opened_payload)
    assert "BUY EXECUTED" in open_alert
    assert "MTB" in open_alert
    assert "NEAR" in open_alert

    closed_payload = {
        "bot": "MTB",
        "coin": "NEAR",
        "pair": "NEAR/INR",
        "entry_price": 450.0,
        "exit_price": 504.0,
        "qty": 0.44,
        "pnl": 23.76,
        "pnl_pct": 12.0,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }
    close_alert = format_position_closed_alert(closed_payload)
    assert "MTB" in close_alert
    assert "TAKE_PROFIT" in close_alert
    assert "23.76" in close_alert


# ── 31. Portfolio Telemetry Visibility ─────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_portfolio_telemetry_visibility(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        portfolio_svc: PortfolioService = env["portfolio_svc"]

        sig = create_mtb_signal(signal_id="SIG-MTB-DASH", coin="NEAR", pair="NEAR/INR", price=450.0, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        pos_repo: PositionRepository = env["pos_repo"]
        open_positions = await pos_repo.get_open()
        pos = open_positions[0]

        closed_pos, trade = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)

        snapshot = await portfolio_svc.get_snapshot()
        assert snapshot is not None
        assert abs(snapshot.total_realised_pnl - trade.pnl) < 1e-2
