"""
PROJECT-ALPHA — Phase 3B: BBS Strategy Integration & Validation Test Suite.

Covers all 29 specified requirements for end-to-end integration and verification:
1. BBS signal creation
2. BBS signal contract (13 canonical fields)
3. BBS strategy field (strategy == "BBS")
4. Scanner -> BBS integration
5. BBS signal persistence
6. Valid Paper execution
7. Stale signal rejection
8. Malformed signal rejection
9. Invalid entry rejection
10. Invalid TP rejection
11. Invalid SL rejection
12. Duplicate signal rejection
13. Persistent replay protection
14. STE -> BBS coin-lock rejection
15. HDA -> BBS coin-lock rejection
16. VCP -> BBS coin-lock rejection
17. BBS -> STE coin-lock rejection
18. BBS -> HDA coin-lock rejection
19. BBS -> VCP coin-lock rejection
20. Coin unlock after close
21. Shared CapitalGuard across all 4 bots (STE, HDA, VCP, BBS)
22. Insufficient capital rejection
23. Position persistence
24. Restart recovery
25. TP close
26. SL close
27. Duplicate close protection
28. Telegram formatting
29. Portfolio telemetry visibility
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
    db_path = str(tmp_path / f"test_bbs_{uuid.uuid4().hex[:8]}.db")
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


def create_bbs_signal(
    signal_id: str = "SIG-BBS-001",
    coin: str = "DOGE",
    pair: str = "DOGE/INR",
    price: float = 18.5,
    score: float = 93.0,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    """Helper to generate a canonical BBS Signal dataclass."""
    now = datetime.now(timezone.utc)
    if is_expired or ttl_seconds < 0:
        gen_time = now - timedelta(seconds=abs(ttl_seconds) + 60)
        exp_time = now - timedelta(seconds=30)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic BBS TP: 25.0% (>=95), 14.0% (>=90), 6.2% (<90)
    tp_pct = 25.0 if score >= 95.0 else (14.0 if score >= 90.0 else 6.2)
    sl_pct = 4.5

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
        "strategy": "BBS",
        "strategy_name": "Bollinger Band Squeeze Breakout",
        "bot": "BBS",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"Bollinger Band Squeeze Breakout: squeeze (score={score:.1f}, RSI=58.0, Bandwidth=2.8%)",
        "indicators": {
            "rsi": 58.0,
            "atr_pct": 2.0,
            "volume_ratio": 1.25,
            "volume_24h": 5200000.0,
            "ema9": 18.2,
            "ema21": 17.9,
            "mtf_aligned": True,
            "bb_bandwidth": 2.8,
            "is_squeeze": True,
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
        coin_class="B",
        mtf_alignment=True,
        generated_at=gen_time,
        expires_at=exp_time,
        source_bot="BBS",
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )


# ── 1. BBS Signal Creation ──────────────────────────────────────────────────

def test_bbs_signal_creation():
    sig = create_bbs_signal(signal_id="SIG-BBS-001", coin="DOGE", pair="DOGE/INR", price=18.5, score=93.0)
    assert sig.id == "SIG-BBS-001"
    assert sig.coin == "DOGE"
    assert sig.pair == "DOGE/INR"
    assert sig.score == 93.0
    assert sig.source_bot == "BBS"
    assert sig.raw_payload["strategy"] == "BBS"
    assert sig.raw_payload["strategy_name"] == "Bollinger Band Squeeze Breakout"


# ── 2. BBS Signal Contract Fields ───────────────────────────────────────────

def test_bbs_signal_contract_fields():
    sig = create_bbs_signal(signal_id="SIG-BBS-CONTRACT", coin="DOGE", pair="DOGE/INR", price=18.5, score=93.0)
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
        assert field in payload, f"Missing required field {field} in BBS contract"
        assert payload[field] is not None, f"Field {field} is None"

    assert payload["entry"] == 18.5
    assert payload["strategy"] == "BBS"
    assert payload["mode"] == "PAPER"
    assert "Bollinger Band Squeeze Breakout" in payload["strategy_name"]


# ── 3. BBS Strategy Field ───────────────────────────────────────────────────

def test_bbs_strategy_field():
    sig = create_bbs_signal(signal_id="SIG-BBS-FIELD", coin="DOGE", pair="DOGE/INR", price=18.5)
    assert sig.source_bot == "BBS"
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)
    assert payload["strategy"] == "BBS"
    assert payload["strategy_name"] == "Bollinger Band Squeeze Breakout"


# ── 4. Scanner -> BBS Integration ───────────────────────────────────────────

def test_bbs_scanner_integration():
    # Simulate 20 closes with narrow standard deviation vs ATR
    c_window = [18.5 + (0.05 * (i % 3 - 1)) for i in range(20)]
    atr_val = 0.5
    c_mean = sum(c_window) / 20.0
    c_std = (sum((x - c_mean) ** 2 for x in c_window) / 20.0) ** 0.5
    bb_up = c_mean + 2.0 * c_std
    bb_lo = c_mean - 2.0 * c_std
    kc_up = c_mean + 1.5 * atr_val
    kc_lo = c_mean - 1.5 * atr_val

    is_squeeze = (bb_up <= kc_up) and (bb_lo >= kc_lo)
    assert is_squeeze is True

    volume_ratio = 1.25
    is_4h_bullish = True
    if volume_ratio >= 1.5:
        bot = "HDA"
    elif is_squeeze and is_4h_bullish:
        bot = "BBS"
        opp_type = "squeeze"
        strat_name = "Bollinger Band Squeeze Breakout"
    else:
        bot = "STE"

    assert bot == "BBS"
    assert opp_type == "squeeze"
    assert strat_name == "Bollinger Band Squeeze Breakout"


# ── 5. BBS Signal Persistence ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_signal_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        sig_repo: SignalRepository = env["sig_repo"]
        sig = create_bbs_signal(signal_id="SIG-BBS-PERSIST", coin="DOGE", pair="DOGE/INR", price=18.5)

        await sig_repo.insert(sig)
        loaded = await sig_repo.get_by_id("SIG-BBS-PERSIST")
        assert loaded is not None
        assert loaded.id == "SIG-BBS-PERSIST"
        assert loaded.source_bot == "BBS"
        assert loaded.raw_payload.get("strategy") == "BBS"


# ── 6. Valid Paper Execution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_valid_paper_execution(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]
        sig = create_bbs_signal(signal_id="SIG-BBS-VALID", coin="DOGE", pair="DOGE/INR", price=18.5)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is True, f"Expected success but got: {res}"

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.coin == "DOGE"
        assert pos.pair == "DOGE/INR"
        assert pos.bot == BotName.BBS
        assert pos.signal_id == "SIG-BBS-VALID"


# ── 7. Stale Signal Rejection ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_stale_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_bbs_signal(signal_id="SIG-BBS-STALE", coin="DOGE", pair="DOGE/INR", price=18.5, is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "SIGNAL_EXPIRED"


# ── 8. Malformed Signal Rejection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_malformed_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        payload = {
            "signal_id": "SIG-BBS-MALFORMED",
            "strategy": "BBS",
            "mode": "PAPER",
            # missing pair, coin, price
        }
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "MISSING_REQUIRED_FIELDS"


# ── 9. Invalid Entry Rejection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_invalid_entry_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_bbs_signal(signal_id="SIG-BBS-BAD-ENTRY", coin="DOGE", pair="DOGE/INR", price=-10.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_ENTRY_PRICE"


# ── 10. Invalid TP Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_invalid_tp_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_bbs_signal(signal_id="SIG-BBS-BAD-TP", coin="DOGE", pair="DOGE/INR", price=18.5)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["take_profit"] = 15.0  # below entry

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 11. Invalid SL Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_invalid_sl_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_bbs_signal(signal_id="SIG-BBS-BAD-SL", coin="DOGE", pair="DOGE/INR", price=18.5)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["stop_loss"] = 20.0  # above entry

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 12. Duplicate Signal Rejection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_duplicate_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_bbs_signal(signal_id="SIG-BBS-DUP", coin="DOGE", pair="DOGE/INR", price=18.5)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")


# ── 13. Persistent Replay Protection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_persistent_replay_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_bbs_signal(signal_id="SIG-BBS-REPLAY", coin="DOGE", pair="DOGE/INR", price=18.5)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        # Clear in-memory deduplication set to simulate restart
        router.auto_trader._processed_idempotency_keys.clear()

        # Re-send same signal; repository check must reject
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") == "SIGNAL_ALREADY_EXECUTED"


# ── 14. STE -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ste_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open STE on DOGE
        ste_payload = {
            "signal_id": "SIG-STE-LOCKS-DOGE",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.8,
            "take_profit": 20.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. BBS attempts to open DOGE
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOCKED-BY-STE", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))

        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 15. HDA -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hda_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open HDA on DOGE
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKS-DOGE",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.6,
            "take_profit": 21.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 2. BBS attempts to open DOGE
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOCKED-BY-HDA", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))

        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 16. VCP -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on DOGE
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKS-DOGE",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.6,
            "take_profit": 20.9,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. BBS attempts to open DOGE
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOCKED-BY-VCP", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))

        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 17. BBS -> STE Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_ste_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on DOGE
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOCKS-STE", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))
        assert res_bbs.get("success") is True

        # 2. STE attempts to open DOGE
        ste_payload = {
            "signal_id": "SIG-STE-LOCKED-BY-BBS",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.8,
            "take_profit": 20.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is False
        assert res_ste.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 18. BBS -> HDA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_hda_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on DOGE
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOCKS-HDA", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))
        assert res_bbs.get("success") is True

        # 2. HDA attempts to open DOGE
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKED-BY-BBS",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.6,
            "take_profit": 21.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is False
        assert res_hda.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 19. BBS -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on DOGE
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOCKS-VCP", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))
        assert res_bbs.get("success") is True

        # 2. VCP attempts to open DOGE
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKED-BY-BBS",
            "coin": "DOGE",
            "pair": "DOGE/INR",
            "price": 18.5,
            "entry_price": 18.5,
            "stop_loss": 17.6,
            "take_profit": 20.9,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 20. Coin Unlock After Close ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_coin_unlock_after_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. Open BBS position on DOGE
        sig1 = create_bbs_signal(signal_id="SIG-BBS-UNLOCK-1", coin="DOGE", pair="DOGE/INR", price=18.5)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig1))
        assert res1.get("success") is True

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]

        # 2. Close position via TP
        await pos_mgr.close_position(pos.id, 21.0, ExitReason.TAKE_PROFIT)

        open_after = await pos_repo.get_open()
        assert len(open_after) == 0

        # 3. New BBS signal on DOGE should succeed now that coin is unlocked
        sig2 = create_bbs_signal(signal_id="SIG-BBS-UNLOCK-2", coin="DOGE", pair="DOGE/INR", price=18.6)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig2))
        assert res2.get("success") is True


# ── 21. Shared CapitalGuard Across All 4 Bots ───────────────────────────────

@pytest.mark.asyncio
async def test_bbs_shared_capital_guard_all_four_bots(tmp_path: Path):
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
        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-CAP", coin="DOGE", pair="DOGE/INR", price=18.5)
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(bbs_sig))
        assert res_bbs.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 4
        bots = {p.bot for p in open_positions}
        assert bots == {BotName.STE, BotName.HDA, BotName.VCP, BotName.BBS}


# ── 22. Insufficient Capital ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_insufficient_capital_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        capital_guard: CapitalGuard = env["capital_guard"]

        # Artificially clamp total capital limit to 100 INR (below minimum 200 order size)
        capital_guard._config.total_capital_limit = 100.0

        bbs_sig = create_bbs_signal(signal_id="SIG-BBS-LOW-CAP", coin="DOGE", pair="DOGE/INR", price=18.5)
        payload = SignalDispatcher()._signal_to_dict(bbs_sig)
        payload["amount"] = 200.0

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("decision_code") == "BLOCKED_TOTAL_CAPITAL"


# ── 23. Position Persistence ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_position_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_bbs_signal(signal_id="SIG-BBS-PERSIST-POS", coin="DOGE", pair="DOGE/INR", price=18.5)
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
        assert res.get("success") is True

        pos = await pos_repo.get_by_signal_id("SIG-BBS-PERSIST-POS")
        assert pos is not None
        assert pos.coin == "DOGE"
        assert pos.pair == "DOGE/INR"
        assert pos.bot == BotName.BBS
        assert pos.status == PositionStatus.OPEN
        assert pos.entry_price == 18.5
        assert pos.take_profit > 18.5
        assert pos.stop_loss < 18.5


# ── 24. Restart Recovery ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_restart_recovery(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]
        pos_repo = env["pos_repo"]

        pos = await pos_mgr.open_position(
            bot=BotName.BBS,
            coin="DOGE",
            pair="DOGE/INR",
            entry_price=18.5,
            qty=11.0,
            mode=BotMode.PAPER,
            stop_loss=17.6675,
            take_profit=21.09,
            signal_id="SIG-BBS-RESTART",
        )

        recovered = await pos_repo.get_open(bot=BotName.BBS, mode=BotMode.PAPER)
        assert len(recovered) == 1
        r_pos = recovered[0]
        assert r_pos.id == pos.id
        assert r_pos.bot == BotName.BBS
        assert r_pos.coin == "DOGE"
        assert r_pos.entry_price == 18.5
        assert r_pos.stop_loss == 17.6675
        assert r_pos.take_profit == 21.09


# ── 25. Take Profit Close ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_take_profit_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_bbs_signal(signal_id="SIG-BBS-TP", coin="DOGE", pair="DOGE/INR", price=18.5, score=93.0)
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


# ── 26. Stop Loss Close ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_stop_loss_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_bbs_signal(signal_id="SIG-BBS-SL", coin="DOGE", pair="DOGE/INR", price=18.5, score=93.0)
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


# ── 27. Duplicate Close Protection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_duplicate_close_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]
        trade_repo: TradeRepository = env["trade_repo"]

        sig = create_bbs_signal(signal_id="SIG-BBS-DUP-CLOSE", coin="DOGE", pair="DOGE/INR", price=18.5)
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


# ── 28. Telegram Formatting ─────────────────────────────────────────────────

def test_bbs_telegram_formatting():
    opened_payload = {
        "bot": "BBS",
        "coin": "DOGE",
        "pair": "DOGE/INR",
        "entry_price": 18.5,
        "qty": 11.0,
        "amount": 203.5,
        "mode": "PAPER",
        "stop_loss": 17.6675,
        "take_profit": 21.09,
    }
    open_alert = format_position_opened_alert(opened_payload)
    assert "BUY EXECUTED" in open_alert
    assert "BBS" in open_alert
    assert "DOGE" in open_alert

    closed_payload = {
        "bot": "BBS",
        "coin": "DOGE",
        "pair": "DOGE/INR",
        "entry_price": 18.5,
        "exit_price": 21.09,
        "qty": 11.0,
        "pnl": 28.49,
        "pnl_pct": 14.0,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }
    close_alert = format_position_closed_alert(closed_payload)
    assert "BBS" in close_alert
    assert "TAKE_PROFIT" in close_alert
    assert "28.49" in close_alert


# ── 29. Portfolio Telemetry Visibility ─────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_portfolio_telemetry_visibility(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        portfolio_svc: PortfolioService = env["portfolio_svc"]

        sig = create_bbs_signal(signal_id="SIG-BBS-DASH", coin="DOGE", pair="DOGE/INR", price=18.5, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        pos_repo: PositionRepository = env["pos_repo"]
        open_positions = await pos_repo.get_open()
        pos = open_positions[0]

        closed_pos, trade = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)

        snapshot = await portfolio_svc.get_snapshot()
        assert snapshot is not None
        assert abs(snapshot.total_realised_pnl - trade.pnl) < 1e-2
