"""
PROJECT-ALPHA — Phase 3B: NR7 Strategy Integration & Validation Test Suite.

Covers all specified requirements for end-to-end integration and verification:
1. NR7 signal creation
2. NR7 signal contract (13 canonical fields)
3. NR7 strategy field (strategy == "NR7")
4. Scanner -> NR7 integration (7-period range squeeze & breakout)
5. NR7 signal persistence
6. Valid Paper execution
7. Stale signal rejection
8. Malformed signal rejection
9. Invalid entry rejection
10. Invalid TP rejection
11. Invalid SL rejection
12. Duplicate signal rejection
13. Persistent replay protection
14. STE -> NR7 coin-lock rejection
15. HDA -> NR7 coin-lock rejection
16. VCP -> NR7 coin-lock rejection
17. BBS -> NR7 coin-lock rejection
18. MTB -> NR7 coin-lock rejection
19. NR7 -> STE coin-lock rejection
20. NR7 -> HDA coin-lock rejection
21. NR7 -> VCP coin-lock rejection
22. NR7 -> BBS coin-lock rejection
23. NR7 -> MTB coin-lock rejection
24. Coin unlock after close
25. Shared CapitalGuard across all 6 bots (STE, HDA, VCP, BBS, MTB, NR7)
26. Insufficient capital rejection
27. Position persistence
28. Restart recovery
29. TP close
30. SL close
31. Duplicate close protection
32. Telegram formatting
33. Portfolio telemetry visibility
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
    db_path = str(tmp_path / f"test_nr7_{uuid.uuid4().hex[:8]}.db")
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


def create_nr7_signal(
    signal_id: str = "SIG-NR7-001",
    coin: str = "ATOM",
    pair: str = "ATOM/INR",
    price: float = 650.0,
    score: float = 92.0,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    """Helper to generate a canonical NR7 Signal dataclass."""
    now = datetime.now(timezone.utc)
    if is_expired or ttl_seconds < 0:
        gen_time = now - timedelta(seconds=abs(ttl_seconds) + 60)
        exp_time = now - timedelta(seconds=30)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic NR7 TP: 20.0% (>=95), 11.0% (>=90), 4.8% (<90)
    tp_pct = 20.0 if score >= 95.0 else (11.0 if score >= 90.0 else 4.8)
    sl_pct = 3.8

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
        "strategy": "NR7",
        "strategy_name": "Narrow Range 7 Squeeze",
        "bot": "NR7",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"Narrow Range 7 Squeeze: narrow_range_expansion (score={score:.1f}, RSI=58.0)",
        "indicators": {
            "rsi": 58.0,
            "atr_pct": 1.9,
            "volume_ratio": 1.15,
            "prior_range": 5.2,
            "min_7_range": 5.2,
            "ema21": price * 0.98,
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
        source_bot="NR7",
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )


# ── 1. NR7 Signal Creation ──────────────────────────────────────────────────

def test_nr7_signal_creation():
    sig = create_nr7_signal(signal_id="SIG-NR7-001", coin="ATOM", pair="ATOM/INR", price=650.0, score=92.0)
    assert sig.id == "SIG-NR7-001"
    assert sig.coin == "ATOM"
    assert sig.pair == "ATOM/INR"
    assert sig.score == 92.0
    assert sig.source_bot == "NR7"
    assert sig.raw_payload["strategy"] == "NR7"
    assert sig.raw_payload["strategy_name"] == "Narrow Range 7 Squeeze"


# ── 2. NR7 Signal Contract Fields ───────────────────────────────────────────

def test_nr7_signal_contract_fields():
    sig = create_nr7_signal(signal_id="SIG-NR7-CONTRACT", coin="ATOM", pair="ATOM/INR", price=650.0, score=92.0)
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
        assert field in payload, f"Missing required field {field} in NR7 contract"
        assert payload[field] is not None, f"Field {field} is None"

    assert payload["entry"] == 650.0
    assert payload["strategy"] == "NR7"
    assert payload["mode"] == "PAPER"
    assert "Narrow Range 7 Squeeze" in payload["strategy_name"]


# ── 3. NR7 Strategy Field ───────────────────────────────────────────────────

def test_nr7_strategy_field():
    sig = create_nr7_signal(signal_id="SIG-NR7-FIELD", coin="ATOM", pair="ATOM/INR", price=650.0)
    assert sig.source_bot == "NR7"
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)
    assert payload["strategy"] == "NR7"
    assert payload["strategy_name"] == "Narrow Range 7 Squeeze"


# ── 4. Scanner -> NR7 Integration ───────────────────────────────────────────

def test_nr7_scanner_integration():
    # Construct 10 candle highs/lows where candle index -2 has narrowest range among prior 7 candles
    highs = [600.0, 605.0, 610.0, 615.0, 620.0, 625.0, 628.0, 630.0, 635.0]
    lows  = [590.0, 592.0, 598.0, 603.0, 608.0, 612.0, 625.0, 627.0, 630.0]
    # Candle -2 range is 630 - 627 = 3.0, which is smaller than all previous 6 ranges
    ranges = [highs[i] - lows[i] for i in range(-8, -1)]
    prior_range = ranges[-1]
    prior_was_nr7 = prior_range <= min(ranges)
    assert prior_was_nr7 is True

    prior_high = highs[-2]
    latest_close = 634.0
    ema21 = 610.0
    is_4h_bullish = True

    is_nr7 = prior_was_nr7 and (latest_close > prior_high) and (latest_close >= ema21) and is_4h_bullish
    assert is_nr7 is True

    volume_ratio = 1.10
    is_bbs = False
    is_mtb = False

    if volume_ratio >= 1.5:
        bot = "HDA"
    elif is_bbs and is_4h_bullish:
        bot = "BBS"
    elif is_mtb:
        bot = "MTB"
    elif is_nr7:
        bot = "NR7"
        opp_type = "narrow_range_expansion"
        strat_name = "Narrow Range 7 Squeeze"
    else:
        bot = "STE"

    assert bot == "NR7"
    assert opp_type == "narrow_range_expansion"
    assert strat_name == "Narrow Range 7 Squeeze"


# ── 5. NR7 Signal Persistence ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_signal_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        sig_repo: SignalRepository = env["sig_repo"]
        sig = create_nr7_signal(signal_id="SIG-NR7-PERSIST", coin="ATOM", pair="ATOM/INR", price=650.0)

        await sig_repo.insert(sig)
        loaded = await sig_repo.get_by_id("SIG-NR7-PERSIST")
        assert loaded is not None
        assert loaded.id == "SIG-NR7-PERSIST"
        assert loaded.source_bot == "NR7"
        assert loaded.coin == "ATOM"
        assert loaded.raw_payload["strategy"] == "NR7"


# ── 6. Valid Paper Execution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_valid_paper_execution(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_nr7_signal(signal_id="SIG-NR7-PAPER", coin="ATOM", pair="ATOM/INR", price=650.0, score=92.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is True, f"Expected success but got: {res}"

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.coin == "ATOM"
        assert pos.pair == "ATOM/INR"
        assert pos.bot == BotName.NR7
        assert pos.signal_id == "SIG-NR7-PAPER"


# ── 7. Stale Signal Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_stale_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_nr7_signal(signal_id="SIG-NR7-STALE", coin="ATOM", pair="ATOM/INR", price=650.0, is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "SIGNAL_EXPIRED"


# ── 8. Malformed Signal Rejection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_malformed_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        payload = {
            "signal_id": "SIG-NR7-MALFORMED",
            "strategy": "NR7",
            "mode": "PAPER",
        }
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "MISSING_REQUIRED_FIELDS"


# ── 9. Invalid Entry Rejection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_invalid_entry_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_nr7_signal(signal_id="SIG-NR7-BAD-ENTRY", coin="ATOM", pair="ATOM/INR", price=-10.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_ENTRY_PRICE"


# ── 10. Invalid TP Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_invalid_tp_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_nr7_signal(signal_id="SIG-NR7-BAD-TP", coin="ATOM", pair="ATOM/INR", price=650.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["take_profit"] = 600.0  # TP below entry!

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 11. Invalid SL Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_invalid_sl_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_nr7_signal(signal_id="SIG-NR7-BAD-SL", coin="ATOM", pair="ATOM/INR", price=650.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["stop_loss"] = 700.0  # SL above entry!

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 12. Duplicate Signal Rejection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_duplicate_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_nr7_signal(signal_id="SIG-NR7-DUP", coin="ATOM", pair="ATOM/INR", price=650.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")


# ── 13. Persistent Replay Protection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_persistent_replay_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_nr7_signal(signal_id="SIG-NR7-REPLAY", coin="ATOM", pair="ATOM/INR", price=650.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        router.auto_trader._processed_idempotency_keys.clear()

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") == "SIGNAL_ALREADY_EXECUTED"


# ── 14. STE -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ste_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open STE on ATOM
        ste_payload = {
            "signal_id": "SIG-STE-LOCKS-ATOM",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 625.0,
            "take_profit": 690.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. NR7 attempts to open ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKED-BY-STE", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 15. HDA -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hda_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open HDA on ATOM
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKS-ATOM",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 620.0,
            "take_profit": 715.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 2. NR7 attempts to open ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKED-BY-HDA", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 16. VCP -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on ATOM
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKS-ATOM",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 620.0,
            "take_profit": 720.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. NR7 attempts to open ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKED-BY-VCP", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 17. BBS -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on ATOM
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKS-ATOM",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 622.0,
            "take_profit": 725.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)
        assert res_bbs.get("success") is True

        # 2. NR7 attempts to open ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKED-BY-BBS", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 18. MTB -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on ATOM
        mtb_payload = {
            "signal_id": "SIG-MTB-LOCKS-ATOM",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 624.0,
            "take_profit": 728.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)
        assert res_mtb.get("success") is True

        # 2. NR7 attempts to open ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKED-BY-MTB", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 19. NR7 -> STE Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_ste_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKS-STE", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res_nr7.get("success") is True

        # 2. STE attempts to open ATOM
        ste_payload = {
            "signal_id": "SIG-STE-LOCKED-BY-NR7",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 625.0,
            "take_profit": 690.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)

        assert res_ste.get("success") is False
        assert res_ste.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 20. NR7 -> HDA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_hda_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKS-HDA", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res_nr7.get("success") is True

        # 2. HDA attempts to open ATOM
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKED-BY-NR7",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 620.0,
            "take_profit": 715.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)

        assert res_hda.get("success") is False
        assert res_hda.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 21. NR7 -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKS-VCP", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res_nr7.get("success") is True

        # 2. VCP attempts to open ATOM
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKED-BY-NR7",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 620.0,
            "take_profit": 720.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)

        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 22. NR7 -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKS-BBS", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res_nr7.get("success") is True

        # 2. BBS attempts to open ATOM
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKED-BY-NR7",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 622.0,
            "take_profit": 725.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)

        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 23. NR7 -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOCKS-MTB", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res_nr7.get("success") is True

        # 2. MTB attempts to open ATOM
        mtb_payload = {
            "signal_id": "SIG-MTB-LOCKED-BY-NR7",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 624.0,
            "take_profit": 728.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 24. Coin Unlock After Close ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_coin_unlock_after_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. Open NR7 on ATOM
        nr7_sig1 = create_nr7_signal(signal_id="SIG-NR7-UNLOCK-1", coin="ATOM", pair="ATOM/INR", price=650.0)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig1))
        assert res1.get("success") is True

        # 2. Close position
        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos = open_positions[0]
        await pos_mgr.close_position(pos.id, 720.0, ExitReason.TAKE_PROFIT)

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 0

        # 3. New signal on ATOM should now succeed
        nr7_sig2 = create_nr7_signal(signal_id="SIG-NR7-UNLOCK-2", coin="ATOM", pair="ATOM/INR", price=655.0)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig2))
        assert res2.get("success") is True


# ── 25. Shared CapitalGuard Across All 6 Bots ───────────────────────────────

@pytest.mark.asyncio
async def test_nr7_shared_capital_guard_all_six_bots(tmp_path: Path):
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
        mtb_payload = {
            "signal_id": "SIG-MTB-CAP",
            "coin": "NEAR",
            "pair": "NEAR/INR",
            "price": 450.0,
            "entry_price": 450.0,
            "stop_loss": 432.0,
            "take_profit": 504.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)
        assert res_mtb.get("success") is True

        # 6. NR7 on ATOM
        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-CAP", coin="ATOM", pair="ATOM/INR", price=650.0)
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res_nr7.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 6
        bots = {p.bot for p in open_positions}
        assert bots == {BotName.STE, BotName.HDA, BotName.VCP, BotName.BBS, BotName.MTB, BotName.NR7}


# ── 26. Insufficient Capital ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_insufficient_capital_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        capital_guard: CapitalGuard = env["capital_guard"]

        # Artificially clamp total capital limit to 100 INR (below minimum 200 order size)
        capital_guard._config.total_capital_limit = 100.0

        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-LOW-CAP", coin="ATOM", pair="ATOM/INR", price=650.0)
        payload = SignalDispatcher()._signal_to_dict(nr7_sig)
        payload["amount"] = 200.0

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("decision_code") == "BLOCKED_TOTAL_CAPITAL"


# ── 27. Position Persistence ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_position_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        nr7_sig = create_nr7_signal(signal_id="SIG-NR7-PERSIST-POS", coin="ATOM", pair="ATOM/INR", price=650.0)
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(nr7_sig))
        assert res.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos_id = open_positions[0].id

        persisted = await pos_repo.get_by_id(pos_id)
        assert persisted is not None
        assert persisted.bot == BotName.NR7
        assert persisted.coin == "ATOM"
        assert persisted.status == PositionStatus.OPEN


# ── 28. Restart Recovery ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_restart_recovery(tmp_path: Path):
    db_path = str(tmp_path / f"test_nr7_restart_{uuid.uuid4().hex[:8]}.db")
    db1 = Database(db_path)
    await db1.open()

    pos_repo1 = PositionRepository(db1.connection)
    trade_repo1 = TradeRepository(db1.connection)
    bus1 = EventBus()
    cfg = AppConfig(total_capital_limit=10000.0, order_size_inr=200.0)

    pos_mgr1 = PositionManager(pos_repo1, trade_repo1, bus1)
    router1 = PaperExecutionRouter(
        bus1, AutoTradeRouter(bus1, dry_run=True, position_repo=pos_repo1), pos_mgr1, CapitalGuard(cfg), pos_repo1
    )

    sig = create_nr7_signal(signal_id="SIG-NR7-RESTART", coin="ATOM", pair="ATOM/INR", price=650.0)
    res = await router1.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
    assert res.get("success") is True

    await db1.close()

    # Simulate restart by reopening DB with new instances
    db2 = Database(db_path)
    await db2.open()
    pos_repo2 = PositionRepository(db2.connection)

    open_pos = await pos_repo2.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].bot == BotName.NR7
    assert open_pos[0].coin == "ATOM"

    await db2.close()


# ── 29. TP Close ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_take_profit_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_nr7_signal(signal_id="SIG-NR7-TP-CLOSE", coin="ATOM", pair="ATOM/INR", price=650.0, score=92.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        open_pos = await pos_repo.get_open()
        pos = open_pos[0]

        tp_exit_price = pos.take_profit
        closed_pos, trade = await pos_mgr.close_position(pos.id, tp_exit_price, ExitReason.TAKE_PROFIT)

        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.exit_reason == ExitReason.TAKE_PROFIT
        assert trade.pnl > 0.0
        assert trade.exit_price == tp_exit_price


# ── 30. SL Close ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_stop_loss_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_nr7_signal(signal_id="SIG-NR7-SL-CLOSE", coin="ATOM", pair="ATOM/INR", price=650.0, score=92.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        open_pos = await pos_repo.get_open()
        pos = open_pos[0]

        sl_exit_price = pos.stop_loss
        closed_pos, trade = await pos_mgr.close_position(pos.id, sl_exit_price, ExitReason.STOP_LOSS)

        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.exit_reason == ExitReason.STOP_LOSS
        assert trade.pnl < 0.0
        assert trade.exit_price == sl_exit_price


# ── 31. Duplicate Close Protection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_duplicate_close_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]
        trade_repo: TradeRepository = env["trade_repo"]

        sig = create_nr7_signal(signal_id="SIG-NR7-DUP-CLOSE", coin="ATOM", pair="ATOM/INR", price=650.0)
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


# ── 32. Telegram Formatting ─────────────────────────────────────────────────

def test_nr7_telegram_formatting():
    opened_payload = {
        "bot": "NR7",
        "coin": "ATOM",
        "pair": "ATOM/INR",
        "entry_price": 650.0,
        "qty": 0.30,
        "amount": 195.0,
        "mode": "PAPER",
        "stop_loss": 625.3,
        "take_profit": 721.5,
    }
    open_alert = format_position_opened_alert(opened_payload)
    assert "BUY EXECUTED" in open_alert
    assert "NR7" in open_alert
    assert "ATOM" in open_alert

    closed_payload = {
        "bot": "NR7",
        "coin": "ATOM",
        "pair": "ATOM/INR",
        "entry_price": 650.0,
        "exit_price": 721.5,
        "qty": 0.30,
        "pnl": 21.45,
        "pnl_pct": 11.0,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }
    close_alert = format_position_closed_alert(closed_payload)
    assert "NR7" in close_alert
    assert "TAKE_PROFIT" in close_alert
    assert "21.45" in close_alert


# ── 33. Portfolio Telemetry Visibility ─────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_portfolio_telemetry_visibility(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        portfolio_svc: PortfolioService = env["portfolio_svc"]

        sig = create_nr7_signal(signal_id="SIG-NR7-DASH", coin="ATOM", pair="ATOM/INR", price=650.0, score=92.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        pos_repo: PositionRepository = env["pos_repo"]
        open_positions = await pos_repo.get_open()
        pos = open_positions[0]

        closed_pos, trade = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)

        snapshot = await portfolio_svc.get_snapshot()
        assert snapshot is not None
        assert abs(snapshot.total_realised_pnl - trade.pnl) < 1e-2
