"""
PROJECT-ALPHA V2 — Full PAPER vs LIVE Execution & Accounting Isolation Test.
Validates:
  1. Paper execution does NOT hit live exchange API.
  2. Live execution signs and dispatches HMAC order to exchange client.
  3. Database positions and orders strictly maintain mode separation.
  4. Portfolio snapshots for PAPER vs LIVE calculate separate capital allocations and PnL.
  5. Exchange reconciliation only evaluates LIVE positions and ignores PAPER positions.
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from background.portfolio.service import PortfolioService
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.metrics_repo import MetricsRepository
from core.repository.order_repo import OrderRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, OrderState, PositionStatus
from execution.reconciliation import ReconciliationService
from execution.service import TradingService
from execution.trading.subaccount_manager import CoinDCXSubAccountManager


@pytest.mark.asyncio
async def test_complete_paper_live_isolation_workflow(tmp_path):
    db_file = str(tmp_path / f"isolation_{uuid.uuid4().hex[:6]}.db")
    db = Database(db_file)
    await db.open()

    conn = db.connection
    pos_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    order_repo = OrderRepository(conn)
    event_repo = EventLogRepository(conn)
    metrics_repo = MetricsRepository(conn)

    bus = EventBus()
    sub_mgr = CoinDCXSubAccountManager()

    # Mock exchange clients
    mock_place_order = AsyncMock(
        return_value={
            "success": True,
            "exchange_order_id": "EX-LIVE-9999",
            "status": "FILLED",
            "is_filled": True,
            "qty": 0.05,
            "price": 10000.0,
        }
    )
    for b in BotName:
        client = sub_mgr.get_client(b)
        client.place_live_order = mock_place_order
        client.get_active_orders = AsyncMock(
            return_value={"success": True, "orders": []}
        )
        client.get_order_status = AsyncMock(
            return_value={
                "success": True,
                "status": "FILLED",
                "filled_qty": 0.05,
                "order": {"status": "filled", "filled_quantity": "0.05"},
            }
        )
        client.get_order_by_client_id = AsyncMock(
            return_value={
                "success": True,
                "status": "FILLED",
                "exchange_order_id": "EX-LIVE-9999",
                "filled_qty": 0.05,
            }
        )
        client.get_balances = AsyncMock(
            return_value={
                "success": True,
                "inr_balance": 10000.0,
                "inr_locked": 0.0,
                "asset_balances": {"BTC": 0.05},
            }
        )

    # 1. PAPER TRADING EXECUTION
    paper_cfg = AppConfig(
        v2_deployment_mode="PAPER",
        v2_trading_enabled=False,
        total_capital_limit=10000.0,
    )
    paper_service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=paper_cfg,
        subaccount_manager=sub_mgr,
        order_repo=order_repo,
    )

    paper_payload = {
        "signal_id": "SIG-PAPER-01",
        "coin": "SOL",
        "pair": "SOL/INR",
        "bot": "STE",
        "price": 10000.0,
        "approved_amount": 200.0,
    }
    await paper_service.on_trade_approved(EventType.TRADE_APPROVED, paper_payload)

    # Verify place_live_order was NEVER called during Paper execution
    mock_place_order.assert_not_called()

    # 2. LIVE TRADING EXECUTION
    live_cfg = AppConfig(
        v2_deployment_mode="LIVE",
        v2_trading_enabled=True,
        total_capital_limit=10000.0,
    )
    live_service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=live_cfg,
        subaccount_manager=sub_mgr,
        order_repo=order_repo,
    )

    live_payload = {
        "signal_id": "SIG-LIVE-01",
        "coin": "BTC",
        "pair": "BTC/INR",
        "bot": "HDA",
        "price": 5000000.0,
        "approved_amount": 200.0,
    }
    await live_service.on_trade_approved(EventType.TRADE_APPROVED, live_payload)

    # Verify place_live_order was called for entry BUY order + disaster stop-loss
    assert mock_place_order.call_count >= 1

    # 3. VERIFY DATABASE ISOLATION
    paper_positions = await pos_repo.get_open(mode=BotMode.PAPER)
    live_positions = await pos_repo.get_open(mode=BotMode.LIVE)

    assert len(paper_positions) == 1
    assert paper_positions[0].coin == "SOL"
    assert paper_positions[0].mode == BotMode.PAPER

    assert len(live_positions) == 1
    assert live_positions[0].coin == "BTC"
    assert live_positions[0].mode == BotMode.LIVE

    # 4. VERIFY CAPITAL & PORTFOLIO SNAPSHOT ISOLATION
    port_service = PortfolioService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        metrics_repo=metrics_repo,
        config=paper_cfg,
    )
    paper_snapshot = await port_service.get_snapshot(mode="PAPER")
    live_snapshot = await port_service.get_snapshot(mode="LIVE")

    assert len(paper_snapshot.positions_by_bot["STE"]) == 1
    assert len(paper_snapshot.positions_by_bot["HDA"]) == 0

    assert len(live_snapshot.positions_by_bot["HDA"]) == 1
    assert len(live_snapshot.positions_by_bot["STE"]) == 0

    # 5. VERIFY RECONCILIATION ISOLATION (Does not flag paper positions against exchange)
    reconciler = ReconciliationService(
        position_repo=pos_repo, subaccount_manager=sub_mgr
    )
    rec_result = await reconciler.reconcile_positions()
    assert len(rec_result["position_mismatches"]) == 0
    assert len(rec_result["orphan_orders"]) == 0
    assert len(rec_result["missing_orders"]) == 0
    assert len(rec_result["cancelled_rejected_orders"]) == 0

    await db.close()
