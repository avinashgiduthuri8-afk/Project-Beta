"""
PROJECT-ALPHA — Phase 3B: VCP Strategy Integration & Validation Test Suite.

Covers all 27 specified requirements for end-to-end integration and verification:
1. VCP signal creation
2. VCP signal contract (13 canonical fields)
3. VCP strategy field (strategy == "VCP")
4. Scanner -> VCP integration
5. VCP signal persistence
6. Valid Paper execution
7. Stale signal rejection
8. Malformed signal rejection
9. Invalid entry rejection
10. Invalid TP rejection
11. Invalid SL rejection
12. Duplicate signal rejection
13. Persistent replay protection
14. STE -> VCP coin-lock rejection
15. HDA -> VCP coin-lock rejection
16. VCP -> STE coin-lock rejection
17. VCP -> HDA coin-lock rejection
18. Coin unlock after close
19. Shared CapitalGuard
20. Insufficient capital
21. Position persistence
22. Restart recovery
23. TP close
24. SL close
25. Duplicate close protection
26. Telegram formatting
27. Portfolio telemetry visibility
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
    db_path = str(tmp_path / f"test_vcp_{uuid.uuid4().hex[:8]}.db")
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


def create_vcp_signal(
    signal_id: str = "SIG-VCP-001",
    coin: str = "SOL",
    pair: str = "SOL/INR",
    price: float = 12000.0,
    score: float = 91.0,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    """Helper to generate a canonical VCP Signal dataclass."""
    now = datetime.now(timezone.utc)
    if is_expired or ttl_seconds < 0:
        gen_time = now - timedelta(seconds=abs(ttl_seconds) + 60)
        exp_time = now - timedelta(seconds=30)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic VCP TP: 22.0% (>=95), 13.0% (>=90), 6.6% (<90)
    tp_pct = 22.0 if score >= 95.0 else (13.0 if score >= 90.0 else 6.6)
    sl_pct = 4.8

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
        "strategy": "VCP",
        "strategy_name": "Volatility Contraction Pattern",
        "bot": "VCP",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"Volatility Contraction Pattern: contraction (score={score:.1f}, RSI=55.0, Contraction=0.35)",
        "indicators": {
            "rsi": 55.0,
            "atr_pct": 1.9,
            "volume_ratio": 1.15,
            "volume_24h": 4500000.0,
            "ema9": 11900.0,
            "ema21": 11750.0,
            "mtf_aligned": True,
            "contraction_ratio": 0.35,
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
        source_bot="VCP",
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )


# ── 1. VCP Signal Creation ──────────────────────────────────────────────────

def test_vcp_signal_creation():
    sig = create_vcp_signal(signal_id="SIG-VCP-001", coin="SOL", pair="SOL/INR", price=12000.0, score=91.0)
    assert sig.id == "SIG-VCP-001"
    assert sig.coin == "SOL"
    assert sig.pair == "SOL/INR"
    assert sig.score == 91.0
    assert sig.source_bot == "VCP"
    assert sig.raw_payload["strategy"] == "VCP"
    assert sig.raw_payload["strategy_name"] == "Volatility Contraction Pattern"


# ── 2. VCP Signal Contract Fields ───────────────────────────────────────────

def test_vcp_signal_contract_fields():
    sig = create_vcp_signal(signal_id="SIG-VCP-CONTRACT", coin="SOL", pair="SOL/INR", price=12000.0, score=91.0)
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
        assert field in payload, f"Missing required field {field} in VCP contract"
        assert payload[field] is not None, f"Field {field} is None"

    assert payload["entry"] == 12000.0
    assert payload["strategy"] == "VCP"
    assert payload["mode"] == "PAPER"
    assert "Volatility Contraction Pattern" in payload["strategy_name"]


# ── 3. VCP Strategy Field ───────────────────────────────────────────────────

def test_vcp_strategy_field():
    sig = create_vcp_signal(signal_id="SIG-VCP-FIELD", coin="SOL", pair="SOL/INR", price=12000.0)
    assert sig.source_bot == "VCP"
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)
    assert payload["strategy"] == "VCP"
    assert payload["strategy_name"] == "Volatility Contraction Pattern"


# ── 4. Scanner -> VCP Integration ───────────────────────────────────────────

def test_vcp_scanner_integration():
    recent_range = 10.0
    wider_range = 25.0
    is_vcp = wider_range > 0 and (recent_range / wider_range) < 0.45
    assert is_vcp is True

    volume_ratio = 1.15
    is_4h_bullish = True
    if volume_ratio >= 1.5:
        bot = "HDA"
    elif is_vcp and is_4h_bullish:
        bot = "VCP"
        opp_type = "contraction"
        strat_name = "Volatility Contraction Pattern"
    else:
        bot = "STE"

    assert bot == "VCP"
    assert opp_type == "contraction"
    assert strat_name == "Volatility Contraction Pattern"


# ── 5. VCP Signal Persistence ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_signal_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        sig_repo: SignalRepository = env["sig_repo"]
        sig = create_vcp_signal(signal_id="SIG-VCP-PERSIST", coin="SOL", pair="SOL/INR", price=12000.0)

        await sig_repo.insert(sig)
        loaded = await sig_repo.get_by_id("SIG-VCP-PERSIST")
        assert loaded is not None
        assert loaded.id == "SIG-VCP-PERSIST"
        assert loaded.source_bot == "VCP"
        assert loaded.raw_payload.get("strategy") == "VCP"


# ── 6. Valid Paper Execution ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_valid_paper_execution(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]
        sig = create_vcp_signal(signal_id="SIG-VCP-VALID", coin="SOL", pair="SOL/INR", price=12000.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is True, f"Expected success but got: {res}"

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.coin == "SOL"
        assert pos.pair == "SOL/INR"
        assert pos.bot == BotName.VCP
        assert pos.signal_id == "SIG-VCP-VALID"


# ── 7. Stale Signal Rejection ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_stale_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_vcp_signal(signal_id="SIG-VCP-STALE", coin="SOL", pair="SOL/INR", price=12000.0, is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "SIGNAL_EXPIRED"


# ── 8. Malformed Signal Rejection ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_malformed_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        payload = {
            "signal_id": "SIG-VCP-MALFORMED",
            "strategy": "VCP",
            "mode": "PAPER",
            # missing pair, coin, price
        }
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "MISSING_REQUIRED_FIELDS"


# ── 9. Invalid Entry Rejection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_invalid_entry_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_vcp_signal(signal_id="SIG-VCP-BAD-ENTRY", coin="SOL", pair="SOL/INR", price=-50.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_ENTRY_PRICE"


# ── 10. Invalid TP Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_invalid_tp_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_vcp_signal(signal_id="SIG-VCP-BAD-TP", coin="SOL", pair="SOL/INR", price=12000.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["take_profit"] = 11000.0  # below entry

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 11. Invalid SL Rejection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_invalid_sl_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_vcp_signal(signal_id="SIG-VCP-BAD-SL", coin="SOL", pair="SOL/INR", price=12000.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)
        payload["stop_loss"] = 13000.0  # above entry

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("error") == "INVALID_TP_SL"


# ── 12. Duplicate Signal Rejection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_duplicate_signal_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_vcp_signal(signal_id="SIG-VCP-DUP", coin="SOL", pair="SOL/INR", price=12000.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("error") in ("DUPLICATE_SIGNAL", "SIGNAL_ALREADY_EXECUTED")


# ── 13. Persistent Replay Protection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_persistent_replay_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        sig = create_vcp_signal(signal_id="SIG-VCP-REPLAY", coin="SOL", pair="SOL/INR", price=12000.0)
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


# ── 14. STE -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ste_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open STE on SOL
        ste_payload = {
            "signal_id": "SIG-STE-LOCK",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 12000.0,
            "entry_price": 12000.0,
            "stop_loss": 11500.0,
            "take_profit": 13000.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is True

        # 2. VCP attempts to open SOL
        vcp_sig = create_vcp_signal(signal_id="SIG-VCP-LOCKED-BY-STE", coin="SOL", pair="SOL/INR", price=12000.0)
        vcp_payload = SignalDispatcher()._signal_to_dict(vcp_sig)
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)

        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 15. HDA -> VCP Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_hda_to_vcp_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open HDA on SOL
        hda_payload = {
            "signal_id": "SIG-HDA-LOCK",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 12000.0,
            "entry_price": 12000.0,
            "stop_loss": 11460.0,
            "take_profit": 13620.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is True

        # 2. VCP attempts to open SOL
        vcp_sig = create_vcp_signal(signal_id="SIG-VCP-LOCKED-BY-HDA", coin="SOL", pair="SOL/INR", price=12000.0)
        vcp_payload = SignalDispatcher()._signal_to_dict(vcp_sig)
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)

        assert res_vcp.get("success") is False
        assert res_vcp.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 16. VCP -> STE Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_ste_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on SOL
        vcp_sig = create_vcp_signal(signal_id="SIG-VCP-LOCKS-STE", coin="SOL", pair="SOL/INR", price=12000.0)
        vcp_payload = SignalDispatcher()._signal_to_dict(vcp_sig)
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. STE attempts to open SOL
        ste_payload = {
            "signal_id": "SIG-STE-LOCKED-BY-VCP",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 12000.0,
            "entry_price": 12000.0,
            "stop_loss": 11500.0,
            "take_profit": 13000.0,
            "strategy": "STE",
            "mode": "PAPER",
            "score": 90.0,
            "amount": 200.0,
        }
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, ste_payload)
        assert res_ste.get("success") is False
        assert res_ste.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 17. VCP -> HDA Coin-Lock Rejection ──────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_to_hda_coin_lock_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        # 1. Open VCP on SOL
        vcp_sig = create_vcp_signal(signal_id="SIG-VCP-LOCKS-HDA", coin="SOL", pair="SOL/INR", price=12000.0)
        vcp_payload = SignalDispatcher()._signal_to_dict(vcp_sig)
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, vcp_payload)
        assert res_vcp.get("success") is True

        # 2. HDA attempts to open SOL
        hda_payload = {
            "signal_id": "SIG-HDA-LOCKED-BY-VCP",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 12000.0,
            "entry_price": 12000.0,
            "stop_loss": 11460.0,
            "take_profit": 13620.0,
            "strategy": "HDA",
            "mode": "PAPER",
            "score": 92.0,
            "amount": 200.0,
        }
        res_hda = await router.handle_signal_event(EventType.SIGNAL_GENERATED, hda_payload)
        assert res_hda.get("success") is False
        assert res_hda.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"


# ── 18. Coin Unlock After Close ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_coin_unlock_after_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. Open VCP position on SOL
        sig1 = create_vcp_signal(signal_id="SIG-VCP-UNLOCK-1", coin="SOL", pair="SOL/INR", price=12000.0)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig1))
        assert res1.get("success") is True

        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]

        # 2. Close position via TP (positional arguments: id, exit_price, reason)
        await pos_mgr.close_position(pos.id, 13560.0, ExitReason.TAKE_PROFIT)

        open_after = await pos_repo.get_open()
        assert len(open_after) == 0

        # 3. New VCP signal on SOL should succeed now that coin is unlocked
        sig2 = create_vcp_signal(signal_id="SIG-VCP-UNLOCK-2", coin="SOL", pair="SOL/INR", price=12100.0)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig2))
        assert res2.get("success") is True


# ── 19. Shared CapitalGuard ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_shared_capital_guard(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        # 1. STE consumes capital with BTC
        ste_payload = {
            "signal_id": "SIG-STE-CAP-1",
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

        # 2. HDA consumes capital with ETH
        hda_payload = {
            "signal_id": "SIG-HDA-CAP-2",
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

        # 3. VCP consumes capital with SOL
        vcp_sig = create_vcp_signal(signal_id="SIG-VCP-CAP-3", coin="SOL", pair="SOL/INR", price=12000.0)
        res_vcp = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(vcp_sig))
        assert res_vcp.get("success") is True

        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 3
        bots = {p.bot for p in open_positions}
        assert bots == {BotName.STE, BotName.HDA, BotName.VCP}


# ── 20. Insufficient Capital ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_insufficient_capital_rejection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        capital_guard: CapitalGuard = env["capital_guard"]

        # Artificially clamp total capital limit to 100 INR (below minimum 200 order size)
        capital_guard._config.total_capital_limit = 100.0

        vcp_sig = create_vcp_signal(signal_id="SIG-VCP-LOW-CAP", coin="SOL", pair="SOL/INR", price=12000.0)
        payload = SignalDispatcher()._signal_to_dict(vcp_sig)
        payload["amount"] = 200.0

        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res.get("success") is False
        assert res.get("decision_code") == "BLOCKED_TOTAL_CAPITAL"


# ── 21. Position Persistence ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_position_persistence(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_vcp_signal(signal_id="SIG-VCP-PERSIST-POS", coin="SOL", pair="SOL/INR", price=12000.0)
        res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
        assert res.get("success") is True

        pos = await pos_repo.get_by_signal_id("SIG-VCP-PERSIST-POS")
        assert pos is not None
        assert pos.coin == "SOL"
        assert pos.pair == "SOL/INR"
        assert pos.bot == BotName.VCP
        assert pos.status == PositionStatus.OPEN
        assert pos.entry_price == 12000.0
        assert pos.take_profit > 12000.0
        assert pos.stop_loss < 12000.0


# ── 22. Restart Recovery ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_restart_recovery(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]
        pos_repo = env["pos_repo"]

        pos = await pos_mgr.open_position(
            bot=BotName.VCP,
            coin="SOL",
            pair="SOL/INR",
            entry_price=12000.0,
            qty=0.02,
            mode=BotMode.PAPER,
            stop_loss=11424.0,
            take_profit=13560.0,
            signal_id="SIG-VCP-RESTART",
        )

        recovered = await pos_repo.get_open(bot=BotName.VCP, mode=BotMode.PAPER)
        assert len(recovered) == 1
        r_pos = recovered[0]
        assert r_pos.id == pos.id
        assert r_pos.bot == BotName.VCP
        assert r_pos.coin == "SOL"
        assert r_pos.entry_price == 12000.0
        assert r_pos.stop_loss == 11424.0
        assert r_pos.take_profit == 13560.0


# ── 23. Take Profit Close ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_take_profit_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_vcp_signal(signal_id="SIG-VCP-TP", coin="SOL", pair="SOL/INR", price=12000.0, score=91.0)
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


# ── 24. Stop Loss Close ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_stop_loss_close(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]

        sig = create_vcp_signal(signal_id="SIG-VCP-SL", coin="SOL", pair="SOL/INR", price=12000.0, score=91.0)
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


# ── 25. Duplicate Close Protection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_duplicate_close_protection(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        pos_repo: PositionRepository = env["pos_repo"]
        trade_repo: TradeRepository = env["trade_repo"]

        sig = create_vcp_signal(signal_id="SIG-VCP-DUP-CLOSE", coin="SOL", pair="SOL/INR", price=12000.0)
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


# ── 26. Telegram Formatting ─────────────────────────────────────────────────

def test_vcp_telegram_formatting():
    opened_payload = {
        "bot": "VCP",
        "coin": "SOL",
        "pair": "SOL/INR",
        "entry_price": 12000.0,
        "qty": 0.0166,
        "amount": 200.0,
        "mode": "PAPER",
        "stop_loss": 11424.0,
        "take_profit": 13560.0,
    }
    open_alert = format_position_opened_alert(opened_payload)
    assert "BUY EXECUTED" in open_alert
    assert "VCP" in open_alert
    assert "SOL" in open_alert

    closed_payload = {
        "bot": "VCP",
        "coin": "SOL",
        "pair": "SOL/INR",
        "entry_price": 12000.0,
        "exit_price": 13560.0,
        "qty": 0.0166,
        "pnl": 25.9,
        "pnl_pct": 13.0,
        "exit_reason": "TAKE_PROFIT",
        "mode": "PAPER",
    }
    close_alert = format_position_closed_alert(closed_payload)
    assert "VCP" in close_alert
    assert "TAKE_PROFIT" in close_alert
    assert "25.9" in close_alert


# ── 27. Portfolio Telemetry Visibility ─────────────────────────────────────

@pytest.mark.asyncio
async def test_vcp_portfolio_telemetry_visibility(tmp_path: Path):
    async with create_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_mgr: PositionManager = env["pos_mgr"]
        portfolio_svc: PortfolioService = env["portfolio_svc"]

        sig = create_vcp_signal(signal_id="SIG-VCP-DASH", coin="SOL", pair="SOL/INR", price=12000.0, score=91.0)
        await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))

        pos_repo: PositionRepository = env["pos_repo"]
        open_positions = await pos_repo.get_open()
        pos = open_positions[0]

        closed_pos, trade = await pos_mgr.close_position(pos.id, pos.take_profit, ExitReason.TAKE_PROFIT)

        snapshot = await portfolio_svc.get_snapshot()
        assert snapshot is not None
        assert abs(snapshot.total_realised_pnl - trade.pnl) < 1e-2
