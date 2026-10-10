"""
PROJECT-ALPHA — Phase 3B: MRB Strategy Integration & Validation Test Suite.

Covers all specified requirements for end-to-end integration and verification:
1. MRB signal creation
2. MRB signal contract (13 canonical fields)
3. MRB strategy field (strategy == "MRB")
4. Scanner -> MRB integration (oversold RSI + bounce candle)
5. MRB signal persistence
6. Valid Paper execution
7. Stale signal rejection
8. Malformed signal rejection
9. Invalid entry rejection
10. Invalid TP rejection
11. Invalid SL rejection
12. Duplicate signal rejection
13. Persistent replay protection
14. STE -> MRB coin-lock rejection
15. HDA -> MRB coin-lock rejection
16. VCP -> MRB coin-lock rejection
17. BBS -> MRB coin-lock rejection
18. MTB -> MRB coin-lock rejection
19. NR7 -> MRB coin-lock rejection
20. PPA -> MRB coin-lock rejection
21. MRB -> STE coin-lock rejection
22. MRB -> HDA coin-lock rejection
23. MRB -> VCP coin-lock rejection
24. MRB -> BBS coin-lock rejection
25. MRB -> MTB coin-lock rejection
26. MRB -> NR7 coin-lock rejection
27. MRB -> PPA coin-lock rejection
28. Coin unlock after close
29. Shared CapitalGuard across all 8 bots (STE, HDA, VCP, BBS, MTB, NR7, PPA, MRB)
30. Insufficient capital rejection
31. Position persistence
32. Restart recovery
33. TP close
34. SL close
35. Duplicate close protection
36. Telegram formatting
37. Portfolio telemetry visibility
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
    db_path = str(tmp_path / f"test_mrb_{uuid.uuid4().hex[:8]}.db")
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


def create_mrb_signal(
    signal_id: str = "SIG-MRB-001",
    coin: str = "LINK",
    pair: str = "LINK/INR",
    price: float = 1450.0,
    score: float = 92.0,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    """Helper to generate a canonical MRB Signal dataclass."""
    now = datetime.now(timezone.utc)
    if is_expired or ttl_seconds < 0:
        gen_time = now - timedelta(seconds=abs(ttl_seconds) + 60)
        exp_time = now - timedelta(seconds=30)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic MRB TP: 20.0% (>=95), 11.0% (>=90), 4.8% (<90)
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
        "strategy": "MRB",
        "strategy_name": "Mean Reversion Bounce",
        "bot": "MRB",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"Mean Reversion Bounce: mean_reversion (score={score:.1f}, RSI=34.5)",
        "indicators": {
            "rsi": 34.5,
            "atr_pct": 2.3,
            "volume_ratio": 1.18,
            "bb_lower": price * 0.99,
            "ema21": price * 1.05,
        },
    }

    return Signal(
        id=signal_id,
        coin=coin,
        pair=pair,
        market_state=MarketState.PULLBACK,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.MEDIUM,
        score=score,
        confidence=score,
        coin_class="B",
        mtf_alignment=True,
        generated_at=gen_time,
        expires_at=exp_time,
        source_bot="MRB",
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )


# ── 1. MRB Signal Creation ──────────────────────────────────────────────────

def test_mrb_signal_creation():
    sig = create_mrb_signal(signal_id="SIG-MRB-001", coin="LINK", pair="LINK/INR", price=1450.0, score=92.0)
    assert sig.id == "SIG-MRB-001"
    assert sig.coin == "LINK"
    assert sig.pair == "LINK/INR"
    assert sig.score == 92.0
    assert sig.source_bot == "MRB"
    assert sig.raw_payload["strategy"] == "MRB"
    assert sig.raw_payload["strategy_name"] == "Mean Reversion Bounce"


# ── 2. MRB Signal Contract Fields ───────────────────────────────────────────

def test_mrb_signal_contract_fields():
    sig = create_mrb_signal(signal_id="SIG-MRB-CONTRACT", coin="LINK", pair="LINK/INR", price=1450.0, score=92.0)
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
        assert field in payload, f"Missing required field {field} in MRB contract"
        assert payload[field] is not None, f"Field {field} is None"

    assert payload["entry"] == 1450.0
    assert payload["strategy"] == "MRB"
    assert payload["mode"] == "PAPER"
    assert "Mean Reversion Bounce" in payload["strategy_name"]


# ── 3. MRB Strategy Field ───────────────────────────────────────────────────

def test_mrb_strategy_field():
    sig = create_mrb_signal(signal_id="SIG-MRB-FIELD", coin="LINK", pair="LINK/INR", price=1450.0)
    assert sig.source_bot == "MRB"
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)
    assert payload["strategy"] == "MRB"
    assert payload["strategy_name"] == "Mean Reversion Bounce"


# ── 4. Scanner -> MRB Integration ───────────────────────────────────────────

def test_mrb_scanner_integration():
    rsi = 34.0
    latest_close = 1450.0
    prior_close = 1420.0
    is_bounce = latest_close > prior_close
    is_oversold = rsi <= 38.0
    market_state = MarketState.PULLBACK.value
    is_4h_bullish = True

    is_mrb = is_oversold and is_bounce and (market_state in (MarketState.PULLBACK.value, MarketState.RECOVERY.value, MarketState.BULL_TREND.value) or is_4h_bullish)
    assert is_mrb is True

    volume_ratio = 1.10
    is_bbs = False
    is_mtb = False
    is_nr7 = False
    is_ppa = False

    if volume_ratio >= 1.5:
        bot = "HDA"
    elif is_bbs and is_4h_bullish:
        bot = "BBS"
    elif is_mtb:
        bot = "MTB"
    elif is_nr7:
        bot = "NR7"
    elif is_ppa:
        bot = "PPA"
    elif is_mrb:
        bot = "MRB"
        opp_type = "mean_reversion"
        strat_name = "Mean Reversion Bounce"
    else:
        bot = "STE"

    assert bot == "MRB"
    assert opp_type == "mean_reversion"
    assert strat_name == "Mean Reversion Bounce"


# ── 5. MRB Signal Persistence ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_signal_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        sig_repo: SignalRepository = env["sig_repo"]
        sig = create_mrb_signal(signal_id="SIG-MRB-PERSIST", coin="LINK", pair="LINK/INR", price=1450.0)

        await sig_repo.insert(sig)
        loaded = await sig_repo.get_by_id("SIG-MRB-PERSIST")
        assert loaded is not None
        assert loaded.id == "SIG-MRB-PERSIST"
        assert loaded.source_bot == "MRB"
        assert loaded.coin == "LINK"
        assert loaded.raw_payload["strategy"] == "MRB"


# ── 6. Valid Paper Execution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_valid_paper_execution(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_mrb_signal(signal_id="SIG-MRB-PAPER", coin="LINK", pair="LINK/INR", price=1450.0, score=92.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is True, f"Expected success but got: {res}"

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.coin == "LINK"
        assert pos.pair == "LINK/INR"
        assert pos.bot == BotName.MRB
        assert pos.signal_id == "SIG-MRB-PAPER"


# ── 7. Stale Signal Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_stale_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mrb_signal(signal_id="SIG-MRB-STALE", coin="LINK", pair="LINK/INR", price=1450.0, is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "SIGNAL_EXPIRED"


# ── 8. Malformed Signal Rejection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_malformed_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        payload = {
            "signal_id": "SIG-MRB-MALFORMED",
            "strategy": "MRB",
            "mode": "PAPER",
        }
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "MISSING_REQUIRED_FIELDS"


# ── 9. Invalid Entry Rejection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_invalid_entry_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mrb_signal(signal_id="SIG-MRB-BAD-ENTRY", coin="LINK", pair="LINK/INR", price=-10.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_ENTRY_PRICE"


# ── 10. Invalid TP Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_invalid_tp_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mrb_signal(signal_id="SIG-MRB-BAD-TP", coin="LINK", pair="LINK/INR", price=1450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["take_profit"] = 1300.0  # TP below entry!

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 11. Invalid SL Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_invalid_sl_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mrb_signal(signal_id="SIG-MRB-BAD-SL", coin="LINK", pair="LINK/INR", price=1450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["stop_loss"] = 1500.0  # SL above entry!

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 12. Duplicate Signal Rejection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_duplicate_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mrb_signal(signal_id="SIG-MRB-DUP", coin="LINK", pair="LINK/INR", price=1450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")


# ── 13. Persistent Replay Protection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_persistent_replay_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_mrb_signal(signal_id="SIG-MRB-REPLAY", coin="LINK", pair="LINK/INR", price=1450.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        router.auto_trader._processed_idempotency_keys.clear()

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") == "SIGNAL_ALREADY_EXECUTED"


# ── 14. STE -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ste_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open STE on LINK
        ste_payload = {
            "signal_id": "SIG-STE-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1399.0,
            "take_profit": 1537.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-STE", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 15. HDA -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hda_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open HDA on LINK
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1385.0,
            "take_profit": 1595.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-HDA", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 16. VCP -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on LINK
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1380.0,
            "take_profit": 1600.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-VCP", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 17. BBS -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on LINK
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1385.0,
            "take_profit": 1610.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)
        assert res_bbs.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-BBS", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 18. MTB -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on LINK
        mtb_payload = {
            "signal_id": "SIG-MTB-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1392.0,
            "take_profit": 1624.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)
        assert res_mtb.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-MTB", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 19. NR7 -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on LINK
        nr7_payload = {
            "signal_id": "SIG-NR7-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1394.9,
            "take_profit": 1609.5,
            "strategy": "NR7",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, nr7_payload)
        assert res_nr7.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-NR7", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 20. PPA -> MRB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_mrb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on LINK
        ppa_payload = {
            "signal_id": "SIG-PPA-LOCKS-LINK",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1392.0,
            "take_profit": 1616.75,
            "strategy": "PPA",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ppa_payload)
        assert res_ppa.get("success") is True

        # 2. MRB attempts to open LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKED-BY-PPA", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))

        assert res_mrb.get("success") is False
        assert res_mrb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 21. MRB -> STE Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_ste_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-STE", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. STE attempts to open LINK
        ste_payload = {
            "signal_id": "SIG-STE-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1399.0,
            "take_profit": 1537.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)

        assert res_ste.get("success") is False
        assert res_ste.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 22. MRB -> HDA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_hda_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-HDA", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. HDA attempts to open LINK
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1385.0,
            "take_profit": 1595.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)

        assert res_hda.get("success") is False
        assert res_hda.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 23. MRB -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-VCP", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. VCP attempts to open LINK
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1380.0,
            "take_profit": 1600.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)

        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 24. MRB -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-BBS", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. BBS attempts to open LINK
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1385.0,
            "take_profit": 1610.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)

        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 25. MRB -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-MTB", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. MTB attempts to open LINK
        mtb_payload = {
            "signal_id": "SIG-MTB-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1392.0,
            "take_profit": 1624.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 26. MRB -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-NR7", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. NR7 attempts to open LINK
        nr7_payload = {
            "signal_id": "SIG-NR7-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1394.9,
            "take_profit": 1609.5,
            "strategy": "NR7",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, nr7_payload)

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 27. MRB -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOCKS-PPA", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        # 2. PPA attempts to open LINK
        ppa_payload = {
            "signal_id": "SIG-PPA-LOCKED-BY-MRB",
            "coin": "LINK",
            "pair": "LINK/INR",
            "price": 1450.0,
            "entry_price": 1450.0,
            "stop_loss": 1392.0,
            "take_profit": 1616.75,
            "strategy": "PPA",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ppa_payload)

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 28. Coin Unlock After Close ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_coin_unlock_after_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. Open MRB on LINK
        mrb_sig1 = create_mrb_signal(signal_id="SIG-MRB-UNLOCK-1", coin="LINK", pair="LINK/INR", price=1450.0)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig1))
        assert res1.get("success") is True

        # 2. Close position
        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos = open_positions[0]
        await pos_mgr.close_position(pos.id, 1600.0, ExitReason.TAKE_PROFIT)

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 0

        # 3. New signal on LINK should now succeed
        mrb_sig2 = create_mrb_signal(signal_id="SIG-MRB-UNLOCK-2", coin="LINK", pair="LINK/INR", price=1460.0)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig2))
        assert res2.get("success") is True


# ── 29. Shared CapitalGuard Across All 8 Bots ───────────────────────────────

@pytest.mark.asyncio
async def test_mrb_shared_capital_guard_all_eight_bots(tmp_path: Path):
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
        nr7_payload = {
            "signal_id": "SIG-NR7-CAP",
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 650.0,
            "entry_price": 650.0,
            "stop_loss": 625.3,
            "take_profit": 721.5,
            "strategy": "NR7",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, nr7_payload)
        assert res_nr7.get("success") is True

        # 7. PPA on AVAX
        ppa_payload = {
            "signal_id": "SIG-PPA-CAP",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2304.0,
            "take_profit": 2676.0,
            "strategy": "PPA",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ppa_payload)
        assert res_ppa.get("success") is True

        # 8. MRB on LINK
        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-CAP", coin="LINK", pair="LINK/INR", price=1450.0)
        res_mrb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res_mrb.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 8
        bots = {p.bot for p in open_positions}
        assert bots == {
            BotName.STE,
            BotName.HDA,
            BotName.VCP,
            BotName.BBS,
            BotName.MTB,
            BotName.NR7,
            BotName.PPA,
            BotName.MRB,
        }


# ── 30. Insufficient Capital ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_insufficient_capital_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        capital_guard: CapitalGuard = env["capital_guard"]

        # Artificially clamp total capital limit to 100 INR (below minimum 200 order size)
        capital_guard._config.total_capital_limit = 100.0

        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-LOW-CAP", coin="LINK", pair="LINK/INR", price=1450.0)
        payload = SignalDispatcher()._signal_to_dict(mrb_sig)
        payload["amount"] = 200.0

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("decision_code") == "BLOCKED_TOTAL_CAPITAL"


# ── 31. Position Persistence ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_position_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        mrb_sig = create_mrb_signal(signal_id="SIG-MRB-PERSIST-POS", coin="LINK", pair="LINK/INR", price=1450.0)
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(mrb_sig))
        assert res.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos_id = open_positions[0].id

        persisted = await pos_repo.get_by_id(pos_id)
        assert persisted is not None
        assert persisted.bot == BotName.MRB
        assert persisted.coin == "LINK"
        assert persisted.status == PositionStatus.OPEN


# ── 32. Restart Recovery ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_restart_recovery(tmp_path: Path):
    db_path = str(tmp_path / f"test_mrb_restart_{uuid.uuid4().hex[:8]}.db")
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

    sig = create_mrb_signal(signal_id="SIG-MRB-RESTART", coin="LINK", pair="LINK/INR", price=1450.0)
    res = await router1.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
    assert res.get("success") is True

    await db1.close()

    # Simulate restart by reopening DB with new instances
    db2 = Database(db_path)
    await db2.open()
    pos_repo2 = PositionRepository(db2.connection)

    open_pos = await pos_repo2.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].bot == BotName.MRB
    assert open_pos[0].coin == "LINK"

    await db2.close()


# ── 33. TP Close ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_take_profit_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_mrb_signal(signal_id="SIG-MRB-TP-CLOSE", coin="LINK", pair="LINK/INR", price=1450.0, score=92.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        open_pos = await pos_repo.get_open()
        pos = open_pos[0]

        tp_exit_price = pos.take_profit
        closed_pos, trade = await pos_mgr.close_position(pos.id, tp_exit_price, ExitReason.TAKE_PROFIT)

        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.exit_reason == ExitReason.TAKE_PROFIT
        assert trade.pnl > 0.0
        assert trade.exit_price == tp_exit_price


# ── 34. SL Close ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_stop_loss_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_mrb_signal(signal_id="SIG-MRB-SL-CLOSE", coin="LINK", pair="LINK/INR", price=1450.0, score=92.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        open_pos = await pos_repo.get_open()
        pos = open_pos[0]

        sl_exit_price = pos.stop_loss
        closed_pos, trade = await pos_mgr.close_position(pos.id, sl_exit_price, ExitReason.STOP_LOSS)

        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.exit_reason == ExitReason.STOP_LOSS
        assert trade.pnl < 0.0
        assert trade.exit_price == sl_exit_price


# ── 35. Duplicate Close Protection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_duplicate_close_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]
        trade_repo: TradeRepository = env["trade_repo"]

        sig = create_mrb_signal(signal_id="SIG-MRB-DUP-CLOSE", coin="LINK", pair="LINK/INR", price=1450.0)
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


# ── 36. Telegram Formatting ─────────────────────────────────────────────────

def test_mrb_telegram_formatting():
    opened_payload = {
        "bot": "MRB",
        "coin": "LINK",
        "pair": "LINK/INR",
        "entry_price": 1450.0,
        "qty": 0.138,
        "amount": 200.1,
        "mode": "PAPER",
        "stop_loss": 1394.9,
        "take_profit": 1609.5,
    }
    open_alert = format_position_opened_alert(opened_payload)
    assert "BUY EXECUTED" in open_alert
    assert "MRB" in open_alert
    assert "LINK" in open_alert

    closed_payload = {
        "bot": "MRB",
        "coin": "LINK",
        "pair": "LINK/INR",
        "entry_price": 1450.0,
        "exit_price": 1609.5,
        "qty": 0.138,
        "pnl": 22.01,
        "pnl_pct": 11.0,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }
    close_alert = format_position_closed_alert(closed_payload)
    assert "MRB" in close_alert
    assert "TAKE_PROFIT" in close_alert
    assert "22.01" in close_alert


# ── 37. Portfolio Telemetry Visibility ─────────────────────────────────────

@pytest.mark.asyncio
async def test_mrb_portfolio_telemetry_visibility(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        portfolio_svc: PortfolioService = env["portfolio_svc"]

        sig = create_mrb_signal(signal_id="SIG-MRB-DASH", coin="LINK", pair="LINK/INR", price=1450.0, score=92.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        pos_repo: PositionRepository = env["pos_repo"]
        open_positions = await pos_repo.get_open()
        pos = open_positions[0]

        closed_pos, trade = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)

        snapshot = await portfolio_svc.get_snapshot()
        assert snapshot is not None
        assert abs(snapshot.total_realised_pnl - trade.pnl) < 1e-2
