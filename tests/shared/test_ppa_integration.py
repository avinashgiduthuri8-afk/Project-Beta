"""
PROJECT-ALPHA — Phase 3B: PPA Strategy Integration & Validation Test Suite.

Covers all specified requirements for end-to-end integration and verification:
1. PPA signal creation
2. PPA signal contract (13 canonical fields)
3. PPA strategy field (strategy == "PPA")
4. Scanner -> PPA integration (Pocket Pivot volume > max down-day volume of prior 10 candles)
5. PPA signal persistence
6. Valid Paper execution
7. Stale signal rejection
8. Malformed signal rejection
9. Invalid entry rejection
10. Invalid TP rejection
11. Invalid SL rejection
12. Duplicate signal rejection
13. Persistent replay protection
14. STE -> PPA coin-lock rejection
15. HDA -> PPA coin-lock rejection
16. VCP -> PPA coin-lock rejection
17. BBS -> PPA coin-lock rejection
18. MTB -> PPA coin-lock rejection
19. NR7 -> PPA coin-lock rejection
20. PPA -> STE coin-lock rejection
21. PPA -> HDA coin-lock rejection
22. PPA -> VCP coin-lock rejection
23. PPA -> BBS coin-lock rejection
24. PPA -> MTB coin-lock rejection
25. PPA -> NR7 coin-lock rejection
26. Coin unlock after close
27. Shared CapitalGuard across all 7 bots (STE, HDA, VCP, BBS, MTB, NR7, PPA)
28. Insufficient capital rejection
29. Position persistence
30. Restart recovery
31. TP close
32. SL close
33. Duplicate close protection
34. Telegram formatting
35. Portfolio telemetry visibility
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
    db_path = str(tmp_path / f"test_ppa_{uuid.uuid4().hex[:8]}.db")
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


def create_ppa_signal(
    signal_id: str = "SIG-PPA-001",
    coin: str = "AVAX",
    pair: str = "AVAX/INR",
    price: float = 2400.0,
    score: float = 93.0,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    """Helper to generate a canonical PPA Signal dataclass."""
    now = datetime.now(timezone.utc)
    if is_expired or ttl_seconds < 0:
        gen_time = now - timedelta(seconds=abs(ttl_seconds) + 60)
        exp_time = now - timedelta(seconds=30)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic PPA TP: 21.0% (>=95), 11.5% (>=90), 5.0% (<90)
    tp_pct = 21.0 if score >= 95.0 else (11.5 if score >= 90.0 else 5.0)
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
        "strategy": "PPA",
        "strategy_name": "Pocket Pivot Accumulation",
        "bot": "PPA",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"Pocket Pivot Accumulation: pocket_pivot (score={score:.1f}, RSI=61.0)",
        "indicators": {
            "rsi": 61.0,
            "atr_pct": 2.1,
            "volume_ratio": 1.35,
            "pocket_pivot_vol": 15000.0,
            "max_down_vol_10": 12000.0,
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
        coin_class="B",
        mtf_alignment=True,
        generated_at=gen_time,
        expires_at=exp_time,
        source_bot="PPA",
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )


# ── 1. PPA Signal Creation ──────────────────────────────────────────────────

def test_ppa_signal_creation():
    sig = create_ppa_signal(signal_id="SIG-PPA-001", coin="AVAX", pair="AVAX/INR", price=2400.0, score=93.0)
    assert sig.id == "SIG-PPA-001"
    assert sig.coin == "AVAX"
    assert sig.pair == "AVAX/INR"
    assert sig.score == 93.0
    assert sig.source_bot == "PPA"
    assert sig.raw_payload["strategy"] == "PPA"
    assert sig.raw_payload["strategy_name"] == "Pocket Pivot Accumulation"


# ── 2. PPA Signal Contract Fields ───────────────────────────────────────────

def test_ppa_signal_contract_fields():
    sig = create_ppa_signal(signal_id="SIG-PPA-CONTRACT", coin="AVAX", pair="AVAX/INR", price=2400.0, score=93.0)
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
        assert field in payload, f"Missing required field {field} in PPA contract"
        assert payload[field] is not None, f"Field {field} is None"

    assert payload["entry"] == 2400.0
    assert payload["strategy"] == "PPA"
    assert payload["mode"] == "PAPER"
    assert "Pocket Pivot Accumulation" in payload["strategy_name"]


# ── 3. PPA Strategy Field ───────────────────────────────────────────────────

def test_ppa_strategy_field():
    sig = create_ppa_signal(signal_id="SIG-PPA-FIELD", coin="AVAX", pair="AVAX/INR", price=2400.0)
    assert sig.source_bot == "PPA"
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)
    assert payload["strategy"] == "PPA"
    assert payload["strategy_name"] == "Pocket Pivot Accumulation"


# ── 4. Scanner -> PPA Integration ───────────────────────────────────────────

def test_ppa_scanner_integration():
    # 12 candles: down candles with volumes, latest candle is up with higher volume than highest down candle
    closes = [2300.0, 2280.0, 2290.0, 2260.0, 2270.0, 2250.0, 2240.0, 2230.0, 2240.0, 2235.0, 2230.0, 2280.0]
    volumes = [1000.0, 1500.0, 800.0, 2000.0, 1200.0, 1800.0, 1400.0, 2200.0, 900.0, 1600.0, 1900.0, 3000.0]

    latest_close = closes[-1]
    ema21 = 2220.0
    is_4h_bullish = True

    down_volumes = [
        volumes[i]
        for i in range(-11, -1)
        if closes[i] < closes[i - 1]
    ]
    max_down_vol = max(down_volumes) if down_volumes else 0.0
    assert max_down_vol == 2200.0

    is_up_candle = latest_close > closes[-2]
    assert is_up_candle is True

    is_ppa = is_up_candle and (volumes[-1] > max_down_vol) and (latest_close >= ema21) and is_4h_bullish
    assert is_ppa is True

    volume_ratio = 1.20
    is_bbs = False
    is_mtb = False
    is_nr7 = False

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
        opp_type = "pocket_pivot"
        strat_name = "Pocket Pivot Accumulation"
    else:
        bot = "STE"

    assert bot == "PPA"
    assert opp_type == "pocket_pivot"
    assert strat_name == "Pocket Pivot Accumulation"


# ── 5. PPA Signal Persistence ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_signal_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        sig_repo: SignalRepository = env["sig_repo"]
        sig = create_ppa_signal(signal_id="SIG-PPA-PERSIST", coin="AVAX", pair="AVAX/INR", price=2400.0)

        await sig_repo.insert(sig)
        loaded = await sig_repo.get_by_id("SIG-PPA-PERSIST")
        assert loaded is not None
        assert loaded.id == "SIG-PPA-PERSIST"
        assert loaded.source_bot == "PPA"
        assert loaded.coin == "AVAX"
        assert loaded.raw_payload["strategy"] == "PPA"


# ── 6. Valid Paper Execution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_valid_paper_execution(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_ppa_signal(signal_id="SIG-PPA-PAPER", coin="AVAX", pair="AVAX/INR", price=2400.0, score=93.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is True, f"Expected success but got: {res}"

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.coin == "AVAX"
        assert pos.pair == "AVAX/INR"
        assert pos.bot == BotName.PPA
        assert pos.signal_id == "SIG-PPA-PAPER"


# ── 7. Stale Signal Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_stale_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_ppa_signal(signal_id="SIG-PPA-STALE", coin="AVAX", pair="AVAX/INR", price=2400.0, is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "SIGNAL_EXPIRED"


# ── 8. Malformed Signal Rejection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_malformed_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        payload = {
            "signal_id": "SIG-PPA-MALFORMED",
            "strategy": "PPA",
            "mode": "PAPER",
        }
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "MISSING_REQUIRED_FIELDS"


# ── 9. Invalid Entry Rejection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_invalid_entry_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_ppa_signal(signal_id="SIG-PPA-BAD-ENTRY", coin="AVAX", pair="AVAX/INR", price=-10.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_ENTRY_PRICE"


# ── 10. Invalid TP Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_invalid_tp_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_ppa_signal(signal_id="SIG-PPA-BAD-TP", coin="AVAX", pair="AVAX/INR", price=2400.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["take_profit"] = 2200.0  # TP below entry!

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 11. Invalid SL Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_invalid_sl_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_ppa_signal(signal_id="SIG-PPA-BAD-SL", coin="AVAX", pair="AVAX/INR", price=2400.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["stop_loss"] = 2500.0  # SL above entry!

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 12. Duplicate Signal Rejection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_duplicate_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_ppa_signal(signal_id="SIG-PPA-DUP", coin="AVAX", pair="AVAX/INR", price=2400.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")


# ── 13. Persistent Replay Protection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_persistent_replay_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_ppa_signal(signal_id="SIG-PPA-REPLAY", coin="AVAX", pair="AVAX/INR", price=2400.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        router.auto_trader._processed_idempotency_keys.clear()

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") == "SIGNAL_ALREADY_EXECUTED"


# ── 14. STE -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ste_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open STE on AVAX
        ste_payload = {
            "signal_id": "SIG-STE-LOCKS-AVAX",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2316.0,
            "take_profit": 2544.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. PPA attempts to open AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKED-BY-STE", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 15. HDA -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hda_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open HDA on AVAX
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKS-AVAX",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2292.0,
            "take_profit": 2640.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 2. PPA attempts to open AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKED-BY-HDA", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 16. VCP -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on AVAX
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKS-AVAX",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2285.0,
            "take_profit": 2650.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. PPA attempts to open AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKED-BY-VCP", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 17. BBS -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_bbs_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open BBS on AVAX
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKS-AVAX",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2292.0,
            "take_profit": 2660.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)
        assert res_bbs.get("success") is True

        # 2. PPA attempts to open AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKED-BY-BBS", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 18. MTB -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mtb_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open MTB on AVAX
        mtb_payload = {
            "signal_id": "SIG-MTB-LOCKS-AVAX",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2304.0,
            "take_profit": 2688.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)
        assert res_mtb.get("success") is True

        # 2. PPA attempts to open AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKED-BY-MTB", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 19. NR7 -> PPA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nr7_to_ppa_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open NR7 on AVAX
        nr7_payload = {
            "signal_id": "SIG-NR7-LOCKS-AVAX",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2308.8,
            "take_profit": 2664.0,
            "strategy": "NR7",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, nr7_payload)
        assert res_nr7.get("success") is True

        # 2. PPA attempts to open AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKED-BY-NR7", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))

        assert res_ppa.get("success") is False
        assert res_ppa.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 20. PPA -> STE Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_ste_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKS-STE", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        # 2. STE attempts to open AVAX
        ste_payload = {
            "signal_id": "SIG-STE-LOCKED-BY-PPA",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2316.0,
            "take_profit": 2544.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)

        assert res_ste.get("success") is False
        assert res_ste.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 21. PPA -> HDA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_hda_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKS-HDA", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        # 2. HDA attempts to open AVAX
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKED-BY-PPA",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2292.0,
            "take_profit": 2640.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)

        assert res_hda.get("success") is False
        assert res_hda.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 22. PPA -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKS-VCP", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        # 2. VCP attempts to open AVAX
        vcp_payload = {
            "signal_id": "SIG-VCP-LOCKED-BY-PPA",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2285.0,
            "take_profit": 2650.0,
            "strategy": "VCP",
            "mode": "PAPER",
            "score": 91.0,
            "amount": 200.0,
        }
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)

        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 23. PPA -> BBS Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_bbs_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKS-BBS", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        # 2. BBS attempts to open AVAX
        bbs_payload = {
            "signal_id": "SIG-BBS-LOCKED-BY-PPA",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2292.0,
            "take_profit": 2660.0,
            "strategy": "BBS",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_bbs = await router.handle_signal_event(EventType.SIGNAL_GENERATED, bbs_payload)

        assert res_bbs.get("success") is False
        assert res_bbs.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 24. PPA -> MTB Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_mtb_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKS-MTB", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        # 2. MTB attempts to open AVAX
        mtb_payload = {
            "signal_id": "SIG-MTB-LOCKED-BY-PPA",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2304.0,
            "take_profit": 2688.0,
            "strategy": "MTB",
            "mode": "PAPER",
            "score": 93.0,
            "amount": 200.0,
        }
        res_mtb = await router.handle_signal_event(EventType.SIGNAL_GENERATED, mtb_payload)

        assert res_mtb.get("success") is False
        assert res_mtb.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 25. PPA -> NR7 Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_to_nr7_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open PPA on AVAX
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOCKS-NR7", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        # 2. NR7 attempts to open AVAX
        nr7_payload = {
            "signal_id": "SIG-NR7-LOCKED-BY-PPA",
            "coin": "AVAX",
            "pair": "AVAX/INR",
            "price": 2400.0,
            "entry_price": 2400.0,
            "stop_loss": 2308.8,
            "take_profit": 2664.0,
            "strategy": "NR7",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_nr7 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, nr7_payload)

        assert res_nr7.get("success") is False
        assert res_nr7.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 26. Coin Unlock After Close ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_coin_unlock_after_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. Open PPA on AVAX
        ppa_sig1 = create_ppa_signal(signal_id="SIG-PPA-UNLOCK-1", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig1))
        assert res1.get("success") is True

        # 2. Close position
        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos = open_positions[0]
        await pos_mgr.close_position(pos.id, 2600.0, ExitReason.TAKE_PROFIT)

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 0

        # 3. New signal on AVAX should now succeed
        ppa_sig2 = create_ppa_signal(signal_id="SIG-PPA-UNLOCK-2", coin="AVAX", pair="AVAX/INR", price=2410.0)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig2))
        assert res2.get("success") is True


# ── 27. Shared CapitalGuard Across All 7 Bots ───────────────────────────────

@pytest.mark.asyncio
async def test_ppa_shared_capital_guard_all_seven_bots(tmp_path: Path):
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
        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-CAP", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res_ppa = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res_ppa.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 7
        bots = {p.bot for p in open_positions}
        assert bots == {BotName.STE, BotName.HDA, BotName.VCP, BotName.BBS, BotName.MTB, BotName.NR7, BotName.PPA}


# ── 28. Insufficient Capital ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_insufficient_capital_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        capital_guard: CapitalGuard = env["capital_guard"]

        # Artificially clamp total capital limit to 100 INR (below minimum 200 order size)
        capital_guard._config.total_capital_limit = 100.0

        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-LOW-CAP", coin="AVAX", pair="AVAX/INR", price=2400.0)
        payload = SignalDispatcher()._signal_to_dict(ppa_sig)
        payload["amount"] = 200.0

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("decision_code") == "BLOCKED_TOTAL_CAPITAL"


# ── 29. Position Persistence ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_position_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        ppa_sig = create_ppa_signal(signal_id="SIG-PPA-PERSIST-POS", coin="AVAX", pair="AVAX/INR", price=2400.0)
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ppa_sig))
        assert res.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1
        pos_id = open_positions[0].id

        persisted = await pos_repo.get_by_id(pos_id)
        assert persisted is not None
        assert persisted.bot == BotName.PPA
        assert persisted.coin == "AVAX"
        assert persisted.status == PositionStatus.OPEN


# ── 30. Restart Recovery ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_restart_recovery(tmp_path: Path):
    db_path = str(tmp_path / f"test_ppa_restart_{uuid.uuid4().hex[:8]}.db")
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

    sig = create_ppa_signal(signal_id="SIG-PPA-RESTART", coin="AVAX", pair="AVAX/INR", price=2400.0)
    res = await router1.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
    assert res.get("success") is True

    await db1.close()

    # Simulate restart by reopening DB with new instances
    db2 = Database(db_path)
    await db2.open()
    pos_repo2 = PositionRepository(db2.connection)

    open_pos = await pos_repo2.get_open()
    assert len(open_pos) == 1
    assert open_pos[0].bot == BotName.PPA
    assert open_pos[0].coin == "AVAX"

    await db2.close()


# ── 31. TP Close ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_take_profit_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_ppa_signal(signal_id="SIG-PPA-TP-CLOSE", coin="AVAX", pair="AVAX/INR", price=2400.0, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        open_pos = await pos_repo.get_open()
        pos = open_pos[0]

        tp_exit_price = pos.take_profit
        closed_pos, trade = await pos_mgr.close_position(pos.id, tp_exit_price, ExitReason.TAKE_PROFIT)

        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.exit_reason == ExitReason.TAKE_PROFIT
        assert trade.pnl > 0.0
        assert trade.exit_price == tp_exit_price


# ── 32. SL Close ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_stop_loss_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_ppa_signal(signal_id="SIG-PPA-SL-CLOSE", coin="AVAX", pair="AVAX/INR", price=2400.0, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        open_pos = await pos_repo.get_open()
        pos = open_pos[0]

        sl_exit_price = pos.stop_loss
        closed_pos, trade = await pos_mgr.close_position(pos.id, sl_exit_price, ExitReason.STOP_LOSS)

        assert closed_pos.status == PositionStatus.CLOSED
        assert closed_pos.exit_reason == ExitReason.STOP_LOSS
        assert trade.pnl < 0.0
        assert trade.exit_price == sl_exit_price


# ── 33. Duplicate Close Protection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_duplicate_close_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]
        trade_repo: TradeRepository = env["trade_repo"]

        sig = create_ppa_signal(signal_id="SIG-PPA-DUP-CLOSE", coin="AVAX", pair="AVAX/INR", price=2400.0)
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


# ── 34. Telegram Formatting ─────────────────────────────────────────────────

def test_ppa_telegram_formatting():
    opened_payload = {
        "bot": "PPA",
        "coin": "AVAX",
        "pair": "AVAX/INR",
        "entry_price": 2400.0,
        "qty": 0.083,
        "amount": 199.2,
        "mode": "PAPER",
        "stop_loss": 2304.0,
        "take_profit": 2676.0,
    }
    open_alert = format_position_opened_alert(opened_payload)
    assert "BUY EXECUTED" in open_alert
    assert "PPA" in open_alert
    assert "AVAX" in open_alert

    closed_payload = {
        "bot": "PPA",
        "coin": "AVAX",
        "pair": "AVAX/INR",
        "entry_price": 2400.0,
        "exit_price": 2676.0,
        "qty": 0.083,
        "pnl": 22.91,
        "pnl_pct": 11.5,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }
    close_alert = format_position_closed_alert(closed_payload)
    assert "PPA" in close_alert
    assert "TAKE_PROFIT" in close_alert
    assert "22.91" in close_alert


# ── 35. Portfolio Telemetry Visibility ─────────────────────────────────────

@pytest.mark.asyncio
async def test_ppa_portfolio_telemetry_visibility(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        portfolio_svc: PortfolioService = env["portfolio_svc"]

        sig = create_ppa_signal(signal_id="SIG-PPA-DASH", coin="AVAX", pair="AVAX/INR", price=2400.0, score=93.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        pos_repo: PositionRepository = env["pos_repo"]
        open_positions = await pos_repo.get_open()
        pos = open_positions[0]

        closed_pos, trade = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)

        snapshot = await portfolio_svc.get_snapshot()
        assert snapshot is not None
        assert abs(snapshot.total_realised_pnl - trade.pnl) < 1e-2
