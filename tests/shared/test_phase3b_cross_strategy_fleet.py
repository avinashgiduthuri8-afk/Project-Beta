"""
PROJECT-ALPHA — Phase 3B: Cross-Strategy Portfolio Stress Test.
Validates concurrent cross-bot execution of all 8 bots:
STE, HDA, VCP, BBS, MTB, NR7, PPA, MRB.

Key assertions:
- Cross-bot coin locking priority.
- Total fleet capital limits under multi-bot contention.
- Graceful rejections of signals that exceed limits.
"""

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
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.repository.signal_repo import SignalRepository
from core.repository.event_log_repo import EventLogRepository
from core.repository.order_repo import OrderRepository
from core.types import (
    BotMode, BotName, ExitReason, MarketState, OppType, PositionStatus,
    Priority, RiskLevel, Signal
)
from background.portfolio.service import PortfolioService
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from execution.risk.capital_guard import CapitalGuard
from paper.execution.router import PaperExecutionRouter
from scanner.signals.dispatcher import SignalDispatcher

@asynccontextmanager
async def create_fleet_test_env(tmp_path: Path):
    db_path = str(tmp_path / f"test_fleet_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_path)
    await db.open()

    conn = db.connection
    pos_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    sig_repo = SignalRepository(conn)
    order_repo = OrderRepository(conn)
    event_repo = EventLogRepository(conn)
    bus = EventBus()

    # Configure fleet with total_capital_limit=1000 and order_size=200
    # Exactly 5 trades can be open at once, 6th will fail.
    cfg = AppConfig(
        total_capital_limit=1000.0,
        order_size_inr=200.0,
        max_concurrent_positions=10,
        enforce_single_coin_lock=True,
    )

    pos_mgr = PositionManager(position_repo=pos_repo, trade_repo=trade_repo, bus=bus)
    auto_trader = AutoTradeRouter(bus=bus, dry_run=True, position_repo=pos_repo)
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
            "pos_repo": pos_repo,
            "trade_repo": trade_repo,
            "sig_repo": sig_repo,
            "order_repo": order_repo,
            "event_repo": event_repo,
            "bus": bus,
            "cfg": cfg,
            "pos_mgr": pos_mgr,
            "paper_router": paper_router,
            "capital_guard": capital_guard,
            "portfolio_svc": portfolio_svc
        }
    finally:
        await portfolio_svc.stop()
        await db.close()

def create_bot_signal(bot_name: str, pair: str, price: float, score: float = 95.0) -> Signal:
    """Helper to generate a canonical signal for any of the 8 bots."""
    coin = pair.split("/")[0]
    now = datetime.now(timezone.utc)
    sig_id = f"SIG-{bot_name}-{uuid.uuid4().hex[:4]}"
    
    tp_pct = 15.0
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
        "strategy": bot_name,
        "strategy_name": f"{bot_name} Strategy",
        "bot": bot_name,
        "timeframe": "1h",
        "ttl_seconds": 300,
        "signal_reason": f"Fleet test {bot_name}",
        "indicators": {"rsi": 50.0},
    }

    return Signal(
        id=sig_id,
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
        generated_at=now,
        expires_at=now + timedelta(seconds=300),
        source_bot=bot_name,
        mode=BotMode.PAPER,
        raw_payload=raw_payload,
    )

@pytest.mark.asyncio
async def test_fleet_cross_bot_coin_locking(tmp_path: Path):
    """Test that all 8 bots respect the single coin lock."""
    async with create_fleet_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]
        
        # Open STE on BTC
        ste_sig = create_bot_signal("STE", "BTC/INR", 8000000.0)
        res_ste = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(ste_sig))
        assert res_ste.get("success") is True
        
        # Verify 7 other bots are rejected when attempting to open BTC
        other_bots = ["HDA", "VCP", "BBS", "MTB", "NR7", "PPA", "MRB"]
        for bot in other_bots:
            sig = create_bot_signal(bot, "BTC/INR", 8000000.0)
            res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
            assert res.get("success") is False
            assert res.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"
            
        positions = await pos_repo.get_open()
        assert len(positions) == 1
        assert positions[0].bot == BotName.STE

@pytest.mark.asyncio
async def test_fleet_capital_limit_contention(tmp_path: Path):
    """Test that exactly 5 trades succeed, and the 6th fails due to capital limit, across multiple bots."""
    async with create_fleet_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]
        
        bots = ["STE", "HDA", "VCP", "BBS", "MTB", "NR7", "PPA", "MRB"]
        pairs = ["BTC/INR", "ETH/INR", "SOL/INR", "NEAR/INR", "ADA/INR", "XRP/INR", "DOT/INR", "LTC/INR"]
        
        success_count = 0
        rejection_count = 0
        
        for bot, pair in zip(bots, pairs):
            sig = create_bot_signal(bot, pair, 100.0)
            res = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig))
            
            if res.get("success"):
                success_count += 1
            else:
                rejection_count += 1
                assert res.get("decision_code") in ["INSUFFICIENT_FUNDS", "CAPITAL_LIMIT_EXCEEDED", "INSUFFICIENT_CAPITAL", "BLOCKED_TOTAL_CAPITAL"]
                
        # Total capital = 1000. Each trade takes 200 notional => exactly 5 should succeed
        assert success_count == 5
        assert rejection_count == 3
        
        positions = await pos_repo.get_open()
        assert len(positions) == 5

@pytest.mark.asyncio
async def test_fleet_graceful_rejection_and_unlock(tmp_path: Path):
    """Test that a coin can be traded by a different bot after the first position is closed."""
    async with create_fleet_test_env(tmp_path) as env:
        router: PaperExecutionRouter = env["paper_router"]
        pos_repo: PositionRepository = env["pos_repo"]
        pos_mgr: PositionManager = env["pos_mgr"]
        
        # MTB opens SOL
        sig1 = create_bot_signal("MTB", "SOL/INR", 12000.0)
        res1 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig1))
        assert res1.get("success") is True
        
        positions = await pos_repo.get_open()
        assert len(positions) == 1
        pos = positions[0]
        
        # PPA tries SOL and is rejected
        sig2 = create_bot_signal("PPA", "SOL/INR", 12100.0)
        res2 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig2))
        assert res2.get("success") is False
        assert res2.get("decision_code") == "OPPORTUNITY_LOCKED_ACTIVE_PAIR"
        
        # MTB position is closed via TP
        await pos_mgr.close_position(pos.id, 14000.0, ExitReason.TAKE_PROFIT)
        
        open_after = await pos_repo.get_open()
        assert len(open_after) == 0
        
        # PPA tries SOL again and succeeds
        sig3 = create_bot_signal("PPA", "SOL/INR", 14000.0)
        res3 = await router.handle_signal_event(EventType.SIGNAL_GENERATED, SignalDispatcher()._signal_to_dict(sig3))
        assert res3.get("success") is True

