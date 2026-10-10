"""
PROJECT-ALPHA V2 — Full End-to-End Pipeline Integration Test.
Validates the complete autonomous flow:
  Scanner Signal -> AI Evaluation -> Risk Approval -> Paper Execution -> Portfolio Snapshot -> Exit Lifecycle
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from background.ai.service import AIIntelligenceService
from background.portfolio.service import PortfolioService
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.bus.subscribers import register_all
from core.config import AppConfig
from core.repository.ai_repo import AIAnalysisRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.metrics_repo import MetricsRepository
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
    OrderState,
    PositionStatus,
    Priority,
    RiskLevel,
    Signal,
)
from execution.risk.service import RiskService
from execution.service import TradingService
from execution.trading.subaccount_manager import CoinDCXSubAccountManager


@pytest.mark.asyncio
async def test_full_scanner_ai_risk_paper_execution_pipeline(tmp_path):
    """
    Test complete lifecycle from Scanner Signal Generation to AI evaluation,
    Risk checks, Paper order execution, and Portfolio aggregation.
    """
    db_file = str(tmp_path / f"e2e_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_file)
    await db.open()

    conn = db.connection
    signal_repo = SignalRepository(conn)
    ai_repo = AIAnalysisRepository(conn)
    position_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    order_repo = OrderRepository(conn)
    metrics_repo = MetricsRepository(conn)
    event_log_repo = EventLogRepository(conn)

    bus = EventBus()
    cfg = AppConfig(
        v2_deployment_mode="PAPER",
        v2_trading_enabled=False,
        total_capital_limit=10000.0,
        order_size_inr=200.0,
        enforce_single_coin_lock=True,
    )

    subaccount_mgr = CoinDCXSubAccountManager()
    for bot in BotName:
        client = subaccount_mgr.get_client(bot)
        client.place_live_order = AsyncMock()

    ai_service = AIIntelligenceService(
        bus=bus,
        ai_repo=ai_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        signal_repo=signal_repo,
    )

    risk_service = RiskService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
    )

    trading_service = TradingService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=cfg,
        subaccount_manager=subaccount_mgr,
        order_repo=order_repo,
    )

    portfolio_service = PortfolioService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        metrics_repo=metrics_repo,
        config=cfg,
    )

    register_all(
        bus,
        ai_service=ai_service,
        risk_service=risk_service,
        trading_service=trading_service,
        portfolio_service=portfolio_service,
    )

    await ai_service.start()
    await risk_service.start()
    await trading_service.start()
    await portfolio_service.start()

    # Track published bus events
    received_events = []

    async def capture_event(event_type: EventType, payload: dict):
        received_events.append((event_type, payload))

    bus.subscribe(EventType.SIGNAL_AI_CONFIRMED, capture_event)
    bus.subscribe(EventType.TRADE_APPROVED, capture_event)
    bus.subscribe(EventType.TRADE_EXECUTED, capture_event)
    bus.subscribe(EventType.POSITION_OPENED, capture_event)
    bus.subscribe(EventType.PORTFOLIO_UPDATED, capture_event)

    # 1. Scanner generates a high conviction signal
    now = datetime.now(timezone.utc)
    sig_id = f"SIG-{uuid.uuid4().hex[:8]}"
    signal = Signal(
        id=sig_id,
        coin="SOL",
        pair="SOL/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.ELITE,
        risk_level=RiskLevel.MEDIUM,
        score=92,
        confidence=90,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now + timedelta(hours=4),
        source_bot="STE",
        raw_payload={
            "bot": "STE",
            "price": 12000.0,
            "stop_loss": 11640.0,
            "take_profit": 13200.0,
            "suggested_amount_inr": 200.0,
            "eval_breakdown": {
                "chart_structure": 30.0,
                "technical_indicators": 32.0,
                "market_sentiment": 15.0,
                "news_events": 15.0,
            },
        },
    )
    await signal_repo.insert(signal)

    # Publish SIGNAL_GENERATED event
    await bus.publish(
        EventType.SIGNAL_GENERATED,
        {
            "signal_id": signal.id,
            "bot": "STE",
            "coin": signal.coin,
            "pair": signal.pair,
            "score": signal.score,
            "price": 12000.0,
            "suggested_amount_inr": 200.0,
            "stop_loss": 11640.0,
            "take_profit": 13200.0,
            "eval_breakdown": signal.raw_payload["eval_breakdown"],
        },
    )

    # Allow async event processing across bus
    await asyncio.sleep(0.5)

    # 2. Verify AI evaluation occurred and was stored
    ai_record = await ai_repo.get_by_signal_id(sig_id)
    assert ai_record is not None
    assert ai_record.recommendation in ("APPROVE", "WATCH", "REDUCE")

    # 3. Verify Position was opened in PAPER mode
    open_positions = await position_repo.get_open(mode="PAPER")
    assert len(open_positions) == 1
    pos = open_positions[0]
    assert pos.coin == "SOL"
    assert pos.pair == "SOL/INR"
    assert pos.bot == BotName.STE
    assert pos.mode == BotMode.PAPER
    assert pos.status == PositionStatus.OPEN
    assert pos.entry_price == 12000.0

    # 4. Verify Order was created, filled, and linked to position
    assert pos.client_order_id is not None
    ord_record = await order_repo.get_by_client_order_id(pos.client_order_id)
    assert ord_record is not None
    assert ord_record.state == OrderState.FILLED
    assert ord_record.position_id == pos.id

    # 5. Verify Portfolio Snapshot updated
    snapshot = await portfolio_service.get_snapshot(mode="PAPER")
    assert snapshot.total_deployed > 0.0
    assert snapshot.total_cash < 10000.0
    assert abs((snapshot.total_cash + snapshot.total_deployed) - 10000.0) < 1.0

    # 6. Verify Exit Simulation (Take Profit triggered)
    exit_price = 13200.0  # +10% target hit
    await trading_service.position_manager.close_position(
        position_id=pos.id,
        exit_price=exit_price,
        exit_reason=ExitReason.TAKE_PROFIT,
    )

    # Verify position is closed
    closed_pos = await position_repo.get_by_id(pos.id)
    assert closed_pos.status == PositionStatus.CLOSED
    assert closed_pos.exit_price == exit_price
    assert closed_pos.exit_reason == ExitReason.TAKE_PROFIT

    # Verify closed trade recorded
    trades = await trade_repo.get_by_coin("SOL", mode="PAPER")
    assert len(trades) >= 1
    trade = trades[0]
    assert trade.pnl > 0.0  # Profitable trade
    assert trade.exit_reason == ExitReason.TAKE_PROFIT
    assert trade.mode == BotMode.PAPER

    # Teardown
    await portfolio_service.stop()
    await trading_service.stop()
    await risk_service.stop()
    await ai_service.stop()
    await db.close()
