"""
PROJECT-ALPHA — Phase 3B: STE Strategy Integration & Validation Test Suite.

Covers all 16 specified requirements for end-to-end integration and verification:
1. Valid STE signal generation
2. Required signal fields contract
3. Scanner -> STE signal persistence
4. Paper receives STE signal
5. Valid STE execution
6. Stale STE signal rejection
7. Duplicate STE signal rejection
8. Duplicate coin protection
9. Insufficient capital rejection
10. Invalid signal rejection (missing fields, invalid price, invalid TP/SL, invalid strategy)
11. Take Profit execution
12. Stop Loss execution
13. Trade creation (exactly one trade per closed position)
14. P&L reconciliation
15. Restart / persistence idempotency
16. Telegram / telemetry visibility
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
    db_path = str(tmp_path / f"test_ste_{uuid.uuid4().hex[:8]}.db")
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
            "pos_mgr": pos_mgr,
            "auto_trader": auto_trader,
            "capital_guard": capital_guard,
            "paper_router": paper_router,
            "portfolio_svc": portfolio_svc,
            "bus": bus,
            "cfg": cfg,
        }
    finally:
        await portfolio_svc.stop()
        await db.close()


def create_ste_signal(
    signal_id: str = "SIG-STE-001",
    coin: str = "SOL",
    pair: str = "SOL/INR",
    price: float = 12000.0,
    score: int = 92,
    ttl_seconds: int = 300,
    is_expired: bool = False,
) -> Signal:
    now = datetime.now(timezone.utc)
    if is_expired:
        gen_time = now - timedelta(seconds=ttl_seconds + 60)
        exp_time = now - timedelta(seconds=60)
    else:
        gen_time = now
        exp_time = now + timedelta(seconds=ttl_seconds)

    # Dynamic TP & SL calculation matching Phase 3B contract
    tp_pct = 20.0 if score >= 95 else (12.5 if score >= 90 else 5.0)
    sl_pct = 3.5
    tp_price = round(price * (1.0 + tp_pct / 100.0), 8)
    sl_price = round(price * (1.0 - sl_pct / 100.0), 8)

    raw_payload = {
        "price": price,
        "entry_price": price,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "score": score,
        "strategy": "STE",
        "strategy_name": "SuperTrend ATR Range Expansion",
        "bot": "STE",
        "timeframe": "1h",
        "ttl_seconds": ttl_seconds,
        "signal_reason": f"SuperTrend ATR Range Expansion: momentum_trade (score={score}, RSI=62.5, Vol=2.2x)",
        "indicators": {
            "rsi": 62.5,
            "atr_pct": 2.4,
            "volume_ratio": 2.2,
            "volume_24h": 500000.0,
            "ema9": 11950.0,
            "ema21": 11800.0,
            "mtf_aligned": True,
        },
    }

    return Signal(
        id=signal_id,
        coin=coin,
        pair=pair,
        market_state=MarketState.BREAKOUT,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.ELITE if score >= 90 else Priority.HIGH,
        risk_level=RiskLevel.MEDIUM,
        score=score,
        confidence=88,
        coin_class="A",
        mtf_alignment=True,
        generated_at=gen_time,
        expires_at=exp_time,
        mode=BotMode.PAPER,
        source_bot="STE",
        raw_payload=raw_payload,
    )


# ── 1. Valid STE Signal Generation ──────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_valid_signal_generation():
    """Verify that an STE signal is cleanly produced with strategy attributes."""
    sig = create_ste_signal(score=92, price=12000.0)
    assert sig.source_bot == "STE"
    assert sig.coin == "SOL"
    assert sig.pair == "SOL/INR"
    assert sig.is_live is True
    assert sig.raw_payload["strategy"] == "STE"
    assert sig.raw_payload["strategy_name"] == "SuperTrend ATR Range Expansion"
    assert sig.raw_payload["take_profit"] > sig.raw_payload["entry_price"]
    assert sig.raw_payload["stop_loss"] < sig.raw_payload["entry_price"]


# ── 2. Required Signal Fields Contract ──────────────────────────────────────

@pytest.mark.anyio
async def test_ste_required_signal_fields():
    """Verify the STE signal payload fulfills the comprehensive contract."""
    sig = create_ste_signal(signal_id="SIG-CONTRACT-001", score=96, price=100.0)
    dispatcher = SignalDispatcher()
    payload = dispatcher._signal_to_dict(sig)

    required_keys = [
        "signal_id",
        "strategy",
        "mode",
        "pair",
        "coin",
        "timeframe",
        "timestamp",
        "price",
        "entry_price",
        "score",
        "confidence",
        "stop_loss",
        "take_profit",
        "signal_reason",
        "indicators",
        "ttl_seconds",
    ]
    for key in required_keys:
        assert key in payload, f"Missing required contract field: {key}"

    assert payload["strategy"] == "STE"
    assert payload["mode"] == "PAPER"
    assert payload["score"] == 96
    assert payload["take_profit"] == 120.0  # 20% for 95+ score
    assert payload["stop_loss"] == 96.5    # 3.5% SL
    assert "rsi" in payload["indicators"]
    assert "atr_pct" in payload["indicators"]


# ── 3. Scanner -> STE Signal Persistence ────────────────────────────────────

@pytest.mark.anyio
async def test_ste_scanner_signal_persistence(tmp_path: Path):
    """Verify STE signals insert into SQLite SignalRepository and retrieve cleanly."""
    async with create_test_env(tmp_path) as env:
        sig_repo = env["sig_repo"]
        sig = create_ste_signal(signal_id="SIG-PERSIST-001")

        await sig_repo.insert(sig)
        retrieved = await sig_repo.get_by_id("SIG-PERSIST-001")

        assert retrieved is not None
        assert retrieved.id == "SIG-PERSIST-001"
        assert retrieved.coin == "SOL"
        assert retrieved.source_bot == "STE"
        assert retrieved.raw_payload["strategy"] == "STE"

        live_sigs = await sig_repo.get_live()
        assert any(s.id == "SIG-PERSIST-001" for s in live_sigs)


# ── 4. Paper Receives STE Signal ────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_paper_receives_signal(tmp_path: Path):
    """Verify PaperExecutionRouter consumes STE signal via EventBus."""
    async with create_test_env(tmp_path) as env:
        bus = env["bus"]
        router = env["paper_router"]

        received = []

        async def hook(event_type, payload):
            res = await router.handle_signal_event(event_type, payload)
            received.append((payload, res))

        bus.subscribe(EventType.SIGNAL_GENERATED, hook)

        sig = create_ste_signal(signal_id="SIG-INTAKE-001", price=5000.0)
        dispatcher = SignalDispatcher(bus)
        await dispatcher.dispatch(sig)

        assert len(received) >= 1
        paper_payload, result = received[0]
        assert paper_payload["mode"] == "PAPER"
        assert paper_payload["signal_id"] == "SIG-INTAKE-001_PAPER"
        assert result["success"] is True


# ── 5. Valid STE Paper Execution ────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_valid_paper_execution(tmp_path: Path):
    """Verify valid STE signal executes, places dry-run order, and persists position."""
    async with create_test_env(tmp_path) as env:
        router = env["paper_router"]
        pos_repo = env["pos_repo"]

        sig = create_ste_signal(signal_id="SIG-EXEC-001", coin="AVAX", pair="AVAX/INR", price=2500.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        result = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)

        assert result is not None
        assert result.get("success") is True
        assert "position_id" in result

        pos = await pos_repo.get_by_id(result["position_id"])
        assert pos is not None
        assert pos.coin == "AVAX"
        assert pos.pair == "AVAX/INR"
        assert pos.bot == BotName.STE
        assert pos.entry_price == 2500.0
        assert pos.status == PositionStatus.OPEN
        assert pos.mode == BotMode.PAPER
        assert pos.stop_loss == round(2500.0 * 0.965, 8)
        assert pos.take_profit == round(2500.0 * 1.125, 8)


# ── 6. Stale STE Signal Rejection ───────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_stale_signal_rejection(tmp_path: Path):
    """Verify that an expired STE signal is safely rejected with SIGNAL_EXPIRED."""
    async with create_test_env(tmp_path) as env:
        router = env["paper_router"]
        pos_repo = env["pos_repo"]

        sig = create_ste_signal(signal_id="SIG-STALE-001", is_expired=True)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        result = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)

        assert result is not None
        assert result.get("success") is False
        assert result.get("rejected") is True
        assert result.get("error") == "SIGNAL_EXPIRED"

        open_pos = await pos_repo.get_active_positions(mode=BotMode.PAPER)
        assert len(open_pos) == 0


# ── 7. Duplicate STE Signal Rejection ───────────────────────────────────────

@pytest.mark.anyio
async def test_ste_duplicate_signal_rejection(tmp_path: Path):
    """Verify submitting the exact same STE signal twice rejects the duplicate."""
    async with create_test_env(tmp_path) as env:
        router = env["paper_router"]
        pos_repo = env["pos_repo"]

        sig = create_ste_signal(signal_id="SIG-DUP-001", coin="DOT", pair="DOT/INR", price=600.0)
        dispatcher = SignalDispatcher()
        payload = dispatcher._signal_to_dict(sig)

        # First attempt: succeed
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res1.get("success") is True

        # Second attempt: reject as duplicate
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        assert res2.get("success") is False
        assert res2.get("rejected") is True
        assert res2.get("error") == "DUPLICATE_SIGNAL"

        positions = await pos_repo.get_active_positions(mode=BotMode.PAPER)
        assert len(positions) == 1


# ── 8. Duplicate Coin Protection ────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_duplicate_coin_protection(tmp_path: Path):
    """Verify single-coin lock rejects opening a second position for the same coin."""
    async with create_test_env(tmp_path) as env:
        router = env["paper_router"]
        pos_repo = env["pos_repo"]

        sig1 = create_ste_signal(signal_id="SIG-COIN-001", coin="MATIC", pair="MATIC/INR", price=50.0)
        sig2 = create_ste_signal(signal_id="SIG-COIN-002", coin="MATIC", pair="MATIC/INR", price=50.2)

        dispatcher = SignalDispatcher()
        p1 = dispatcher._signal_to_dict(sig1)
        p2 = dispatcher._signal_to_dict(sig2)

        # First trade on MATIC
        r1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p1)
        assert r1.get("success") is True

        # Second trade on MATIC while first is open and not in >3% profit
        r2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p2)
        assert r2.get("success") is False
        assert r2.get("rejected") is True
        assert r2.get("error") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"

        positions = await pos_repo.get_active_positions(mode=BotMode.PAPER)
        assert len(positions) == 1


# ── 9. Insufficient Capital Rejection ───────────────────────────────────────

@pytest.mark.anyio
async def test_ste_insufficient_capital_rejection(tmp_path: Path):
    """Verify orders exceeding total capital limit are rejected with explicit reason."""
    async with create_test_env(tmp_path) as env:
        router = env["paper_router"]
        cfg = env["cfg"]
        cfg.total_capital_limit = 300.0  # Cap at ₹300 total

        sig1 = create_ste_signal(signal_id="SIG-CAP-001", coin="ADA", pair="ADA/INR", price=50.0)
        sig2 = create_ste_signal(signal_id="SIG-CAP-002", coin="XRP", pair="XRP/INR", price=45.0)

        dispatcher = SignalDispatcher()
        p1 = dispatcher._signal_to_dict(sig1)
        p2 = dispatcher._signal_to_dict(sig2)

        # First trade: ₹200 order consumes budget
        r1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p1)
        assert r1.get("success") is True

        # Second trade: ₹200 requested exceeds remaining ₹100
        r2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p2)
        assert r2.get("success") is False
        assert r2.get("rejected") is True
        assert r2.get("error") == "INSUFFICIENT_CAPITAL"


# ── 10. Invalid Signal Rejection ────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_invalid_signal_rejection(tmp_path: Path):
    """Verify malformed signals (missing fields, invalid price, invalid TP/SL, bad strategy) are rejected."""
    async with create_test_env(tmp_path) as env:
        router = env["paper_router"]

        # 1. Missing required fields
        p_missing = {"id": "SIG-BAD-01", "mode": "PAPER"}
        r1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p_missing)
        assert r1["rejected"] is True
        assert r1["error"] == "MISSING_REQUIRED_FIELDS"

        # 2. Invalid price
        p_bad_price = {
            "id": "SIG-BAD-02",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": -10.0,
            "mode": "PAPER",
        }
        r2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p_bad_price)
        assert r2["rejected"] is True
        assert r2["error"] == "INVALID_ENTRY_PRICE"

        # 3. Invalid TP / SL (SL >= entry price)
        p_bad_sl = {
            "id": "SIG-BAD-03",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 100.0,
            "stop_loss": 105.0,
            "mode": "PAPER",
        }
        r3 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p_bad_sl)
        assert r3["rejected"] is True
        assert r3["error"] == "INVALID_TP_SL"

        # 4. Invalid strategy
        p_bad_strat = {
            "id": "SIG-BAD-04",
            "coin": "SOL",
            "pair": "SOL/INR",
            "price": 100.0,
            "strategy": "UNKNOWN_RANDOM_STRAT",
            "mode": "PAPER",
        }
        r4 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, p_bad_strat)
        assert r4["rejected"] is True
        assert r4["error"] == "INVALID_STRATEGY"


# ── 11. Take Profit Execution ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_take_profit_execution(tmp_path: Path):
    """Verify bracket evaluation identifies TAKE_PROFIT and closes position."""
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]

        pos = await pos_mgr.open_position(
            bot=BotName.STE,
            coin="NEAR",
            pair="NEAR/INR",
            entry_price=400.0,
            qty=0.5,
            stop_loss=385.0,
            take_profit=440.0,
            mode=BotMode.PAPER,
        )

        # Price hits TP
        triggers = await pos_mgr.evaluate_brackets("NEAR/INR", 442.0)
        assert len(triggers) == 1
        trig_pos, reason, exit_px = triggers[0]
        assert reason == ExitReason.TAKE_PROFIT
        assert exit_px == 442.0

        closed_pos, trade = await pos_mgr.close_position(
            position_id=pos.id,
            exit_price=exit_px,
            exit_reason=reason,
        )
        assert closed_pos.status == PositionStatus.CLOSED
        assert trade is not None
        assert trade.exit_reason == ExitReason.TAKE_PROFIT


# ── 12. Stop Loss Execution ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_stop_loss_execution(tmp_path: Path):
    """Verify bracket evaluation identifies STOP_LOSS and closes position."""
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]

        pos = await pos_mgr.open_position(
            bot=BotName.STE,
            coin="LINK",
            pair="LINK/INR",
            entry_price=1000.0,
            qty=0.2,
            stop_loss=965.0,
            take_profit=1100.0,
            mode=BotMode.PAPER,
        )

        # Price drops to SL
        triggers = await pos_mgr.evaluate_brackets("LINK/INR", 960.0)
        assert len(triggers) == 1
        trig_pos, reason, exit_px = triggers[0]
        assert reason == ExitReason.STOP_LOSS
        assert exit_px == 960.0

        closed_pos, trade = await pos_mgr.close_position(
            position_id=pos.id,
            exit_price=exit_px,
            exit_reason=reason,
        )
        assert closed_pos.status == PositionStatus.CLOSED
        assert trade is not None
        assert trade.exit_reason == ExitReason.STOP_LOSS


# ── 13. Trade Creation (Exactly One per Close) ──────────────────────────────

@pytest.mark.anyio
async def test_ste_trade_creation_exactly_one(tmp_path: Path):
    """Verify closing an STE position creates exactly ONE trade, idempotent on retry."""
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]
        trade_repo = env["trade_repo"]

        pos = await pos_mgr.open_position(
            bot=BotName.STE,
            coin="UNI",
            pair="UNI/INR",
            entry_price=600.0,
            qty=0.35,
            stop_loss=575.0,
            take_profit=660.0,
            mode=BotMode.PAPER,
        )

        # Close once
        pos1, trade1 = await pos_mgr.close_position(pos.id, 660.0, ExitReason.TAKE_PROFIT)
        assert trade1 is not None

        # Close duplicate call
        pos2, trade2 = await pos_mgr.close_position(pos.id, 660.0, ExitReason.TAKE_PROFIT)
        assert trade2 is not None
        assert trade1.id == trade2.id

        # Check database
        trades = await trade_repo.get_by_position(pos.id)
        assert len(trades) == 1


# ── 14. P&L Reconciliation ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_ste_pnl_reconciliation(tmp_path: Path):
    """Verify realized P&L applies statutory friction and aggregates correctly."""
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]
        portfolio_svc = env["portfolio_svc"]

        entry_px = 1000.0
        exit_px = 1100.0
        qty = 0.5
        notional_entry = entry_px * qty   # 500.0
        notional_exit = exit_px * qty     # 550.0

        pos = await pos_mgr.open_position(
            bot=BotName.STE,
            coin="AAVE",
            pair="AAVE/INR",
            entry_price=entry_px,
            qty=qty,
            stop_loss=965.0,
            take_profit=1100.0,
            mode=BotMode.PAPER,
        )

        _, trade = await pos_mgr.close_position(pos.id, exit_px, ExitReason.TAKE_PROFIT)

        gross_pnl = (exit_px - entry_px) * qty  # +50.0
        expected_friction = (notional_entry + notional_exit) * (0.01572 / 2.0)  # (500 + 550) * 0.00786 = 8.253
        expected_net = round(gross_pnl - expected_friction, 2)  # 41.75

        calc_gross = (trade.exit_price - trade.entry_price) * trade.qty
        assert abs(calc_gross - gross_pnl) < 1e-4
        assert abs(trade.pnl - expected_net) < 1e-2
        assert trade.pair.endswith("/INR")

        # Verify Portfolio aggregator reflects trade
        snapshot = await portfolio_svc.get_snapshot(mode=BotMode.PAPER)
        assert abs(snapshot.total_realised_pnl - expected_net) < 1e-2


# ── 15. Restart / Persistence Idempotency ───────────────────────────────────

@pytest.mark.anyio
async def test_ste_restart_idempotency(tmp_path: Path):
    """Verify that after a process restart, an already-executed signal is rejected via DB lookup."""
    async with create_test_env(tmp_path) as env:
        pos_mgr = env["pos_mgr"]
        pos_repo = env["pos_repo"]
        bus = env["bus"]

        sig_id = "SIG-RESTART-001"
        # Pre-open position in DB with signal_id
        await pos_mgr.open_position(
            bot=BotName.STE,
            coin="ATOM",
            pair="ATOM/INR",
            entry_price=450.0,
            qty=0.5,
            mode=BotMode.PAPER,
            signal_id=sig_id,
        )

        # Fresh AutoTrader and Router with empty memory cache (simulating process restart)
        new_auto_trader = AutoTradeRouter(
            bus=bus,
            dry_run=True,
            position_repo=pos_repo,
        )
        new_router = PaperExecutionRouter(
            bus=bus,
            auto_trader=new_auto_trader,
            position_manager=pos_mgr,
            position_repo=pos_repo,
        )

        replay_payload = {
            "id": sig_id,
            "signal_id": sig_id,
            "coin": "ATOM",
            "pair": "ATOM/INR",
            "price": 450.0,
            "mode": "PAPER",
            "strategy": "STE",
        }

        result = await new_router.handle_signal_event(EventType.SIGNAL_GENERATED, replay_payload)

        assert result.get("success") is False
        assert result.get("rejected") is True
        assert result.get("error") == "SIGNAL_ALREADY_EXECUTED"

        # Confirm still exactly 1 position in DB
        pos_check = await pos_repo.get_by_signal_id(sig_id)
        assert pos_check is not None
        open_positions = await pos_repo.get_open()
        assert len(open_positions) == 1


# ── 16. Telegram / Telemetry Visibility ─────────────────────────────────────

@pytest.mark.anyio
async def test_ste_telegram_telemetry_visibility(tmp_path: Path):
    """Verify Telegram formatters and interactive interface cleanly display STE activity."""
    async with create_test_env(tmp_path) as env:
        pos_repo = env["pos_repo"]
        trade_repo = env["trade_repo"]
        pos_mgr = env["pos_mgr"]
        portfolio_svc = env["portfolio_svc"]

        # 1. Format opened alert
        open_payload = {
            "coin": "SOL",
            "pair": "SOL/INR",
            "bot": "STE",
            "entry_price": 12000.0,
            "qty": 0.02,
            "amount": 240.0,
            "stop_loss": 11580.0,
            "take_profit": 13500.0,
        }
        msg_open = format_position_opened_alert(open_payload)
        assert "BUY EXECUTED" in msg_open
        assert "STE" in msg_open
        assert "SOL" in msg_open

        # 2. Format closed alert
        close_payload = {
            "coin": "SOL",
            "pair": "SOL/INR",
            "bot": "STE",
            "pnl": 25.50,
            "pnl_pct": 10.6,
            "exit_reason": "TAKE_PROFIT",
            "exit_price": 13500.0,
        }
        msg_close = format_position_closed_alert(close_payload)
        assert "STE" in msg_close
        assert "TAKE_PROFIT" in msg_close
        assert "+25.50" in msg_close or "25.50" in msg_close

        # 3. Create active position and closed trade in DB
        pos = await pos_mgr.open_position(
            bot=BotName.STE,
            coin="SOL",
            pair="SOL/INR",
            entry_price=12000.0,
            qty=0.02,
            stop_loss=11580.0,
            take_profit=13500.0,
            mode=BotMode.PAPER,
        )
        await pos_mgr.close_position(pos.id, 13500.0, ExitReason.TAKE_PROFIT)

        # 4. Interactive interface commands
        sent_messages: list[str] = []

        class MockTelegramClient:
            is_configured = True
            async def send_message(self, *args, **kwargs):
                txt = kwargs.get("text") or (args[0] if args else "")
                sent_messages.append(str(txt))
                return True

        mock_client = MockTelegramClient()

        interface = TelegramInteractiveInterface(
            telegram_client=mock_client,
            bus=env["bus"],
            config=env["cfg"],
            position_repo=pos_repo,
            trade_repo=trade_repo,
            portfolio_service=portfolio_svc,
            mode_override="PAPER",
        )

        # Verify positions command
        await interface._send_positions(12345)
        assert len(sent_messages) >= 1
        assert "POSITIONS" in sent_messages[-1]

        # Verify trades command
        await interface._send_trades(12345)
        assert len(sent_messages) >= 2
        assert "TRADES" in sent_messages[-1]
        assert "STE" in sent_messages[-1] or "SOL" in sent_messages[-1]

        # Verify PnL command
        await interface._send_pnl(12345)
        assert len(sent_messages) >= 3
        assert "PROFIT & LOSS" in sent_messages[-1] or "P&L" in sent_messages[-1]

        # Verify winrate command
        await interface._send_winrate(12345)
        assert len(sent_messages) >= 4
        assert "WIN RATE" in sent_messages[-1] or "PERFORMANCE" in sent_messages[-1]
