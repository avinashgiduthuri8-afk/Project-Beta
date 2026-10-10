"""
V2 Real CoinDCX Reconciliation Test Suite.

Verifies that ReconciliationService & TradingService accurately detect:
  1. Orphan orders (active orders on CoinDCX not present in SQLite)
  2. Missing orders (SQLite open positions with order IDs missing on exchange)
  3. Partial fills (quantity discrepancies; auto-aligns SQLite quantity)
  4. Filled orders (confirmed filled orders)
  5. Cancelled/Rejected orders (auto-repairs SQLite position to CLOSED)
  6. Balance mismatches (INR cash + deployed capital discrepancies vs CoinDCX)
  7. Position mismatches (crypto asset holding discrepancies vs SQLite open positions)
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.types import BotMode, BotName, Position, PositionStatus
from execution.reconciliation import ReconciliationService
from execution.trading.subaccount_manager import (
    CoinDCXSubAccountManager,
)


@pytest.fixture
async def test_env():
    mem_uri = f"file:test_recon_{uuid.uuid4().hex}?mode=memory&cache=shared"
    db = Database(path=mem_uri)
    await db.open()

    pos_repo = PositionRepository(db.connection)

    mgr = CoinDCXSubAccountManager()
    rec_service = ReconciliationService(position_repo=pos_repo, subaccount_manager=mgr)

    yield {
        "db": db,
        "pos_repo": pos_repo,
        "mgr": mgr,
        "rec_service": rec_service,
    }
    await db.close()


@pytest.mark.anyio
async def test_reconciliation_clean_state(test_env):
    """Clean state with zero active positions returns IN_SYNC."""
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {},
        }
    )
    client._shared_state["wallet_balance_inr"] = 10000.0

    res = await rec_service.reconcile_positions()
    assert res["status"] == "IN_SYNC"
    assert res["is_clean"] is True
    assert res["mismatches"] == 0
    assert len(res["discrepancies"]) == 0


@pytest.mark.anyio
async def test_reconciliation_detects_orphan_orders(test_env):
    """Detects active orders on CoinDCX that have no corresponding position in SQLite."""
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    # Active order on exchange not in SQLite
    client.get_active_orders = AsyncMock(
        return_value={
            "success": True,
            "orders": [
                {
                    "id": "ex-orphan-999",
                    "client_order_id": "cl-orphan-999",
                    "market": "BTCINR",
                    "side": "buy",
                    "status": "open",
                    "price_per_unit": 7500000.0,
                    "total_quantity": 0.001,
                }
            ],
        }
    )
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["orphan_orders"]) == 1
    assert res["orphan_orders"][0]["exchange_order_id"] == "ex-orphan-999"
    assert res["orphan_orders"][0]["action"] in (
        "CANCELLED_ORPHAN_ORDER",
        "FLAGGED_ORPHAN_ORDER",
    )


@pytest.mark.anyio
async def test_reconciliation_detects_missing_orders(test_env):
    """Detects SQLite positions whose exchange order ID is missing on CoinDCX."""
    pos_repo = test_env["pos_repo"]
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    pos = Position(
        id="pos-missing-101",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=0.5,
        entry_price=10000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id="ex-missing-101",
    )
    await pos_repo.insert(pos)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_order_status = AsyncMock(
        return_value={"success": False, "error": "NOT_FOUND"}
    )
    client.get_order_by_client_id = AsyncMock(
        return_value={"success": False, "error": "NOT_FOUND"}
    )
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {"SOL": 0.5},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["missing_orders"]) == 1
    assert res["missing_orders"][0]["position_id"] == "pos-missing-101"
    assert res["missing_orders"][0]["action"] == "FLAGGED_MISSING_ORDER"


@pytest.mark.anyio
async def test_reconciliation_aligns_partial_fills(test_env):
    """Detects quantity mismatches and auto-aligns SQLite position quantity to exchange filled qty."""
    pos_repo = test_env["pos_repo"]
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    pos = Position(
        id="pos-partial-202",
        bot=BotName.STE,
        coin="ETH",
        pair="ETH/INR",
        qty=0.010,  # SQLite has 0.010
        entry_price=200000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id="ex-partial-202",
    )
    await pos_repo.insert(pos)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    # Exchange says filled_qty is 0.006
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "PARTIALLY_FILLED",
            "filled_qty": 0.006,
            "exchange_order_id": "ex-partial-202",
        }
    )
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {"ETH": 0.006},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["partial_fills"]) == 1
    assert res["partial_fills"][0]["position_id"] == "pos-partial-202"
    assert res["partial_fills"][0]["exchange_filled_qty"] == 0.006

    # Verify SQLite position quantity was updated to 0.006
    updated_pos = await pos_repo.get_by_id("pos-partial-202")
    assert pytest.approx(updated_pos.qty, 1e-6) == 0.006


@pytest.mark.anyio
async def test_reconciliation_repairs_cancelled_orders(test_env):
    """Auto-repairs local position to CLOSED when exchange order status is CANCELLED or REJECTED."""
    pos_repo = test_env["pos_repo"]
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    pos = Position(
        id="pos-canc-303",
        bot=BotName.STE,
        coin="BNB",
        pair="BNB/INR",
        qty=0.01,
        entry_price=70000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id="ex-canc-303",
    )
    await pos_repo.insert(pos)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "CANCELLED",
            "filled_qty": 0.0,
            "exchange_order_id": "ex-canc-303",
        }
    )
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["cancelled_rejected_orders"]) == 1
    assert res["cancelled_rejected_orders"][0]["action"] == "AUTO_REPAIRED_TO_CLOSED"

    # Verify local position is now CLOSED
    active = await pos_repo.get_active_positions()
    assert len(active) == 0


@pytest.mark.anyio
async def test_reconciliation_detects_balance_mismatch(test_env):
    """Detects INR balance discrepancies between internal subaccount manager and CoinDCX."""
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    # Set internal wallet balance to 10000.0
    with client._lock:
        client._shared_state["wallet_balance_inr"] = 10000.0

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    # CoinDCX real balance returns 8500.0 INR
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 8500.0,
            "inr_locked": 0.0,
            "asset_balances": {},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["balance_mismatches"]) == 1
    assert res["balance_mismatches"][0]["difference"] == 1500.0
    assert res["balance_mismatches"][0]["action"] == "BALANCE_ALIGNED_TO_EXCHANGE"

    # Verify internal balance was aligned to 8500.0
    assert client.wallet_balance_inr == 8500.0


@pytest.mark.anyio
async def test_reconciliation_detects_position_asset_mismatch(test_env):
    """Detects discrepancies between SQLite open position quantities and CoinDCX crypto balances."""
    pos_repo = test_env["pos_repo"]
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    # SQLite open position for BTC = 0.005
    pos = Position(
        id="pos-btc-404",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.005,
        entry_price=7800000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        exchange_order_id="ex-btc-404",
    )
    await pos_repo.insert(pos)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "FILLED",
            "filled_qty": 0.005,
            "exchange_order_id": "ex-btc-404",
        }
    )

    # CoinDCX holding says BTC = 0.002 (0.003 difference!)
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {"BTC": 0.002},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["position_mismatches"]) == 1
    assert res["position_mismatches"][0]["coin"] == "BTC"
    assert res["position_mismatches"][0]["local_qty"] == 0.005
    assert res["position_mismatches"][0]["exchange_qty"] == 0.002


@pytest.mark.anyio
async def test_reconciliation_flags_orphan_orders_safely(test_env):
    """Verifies that resting orphan orders on exchange are safely flagged for verification rather than immediately cancelled."""
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    client.get_active_orders = AsyncMock(
        return_value={
            "success": True,
            "orders": [
                {
                    "id": "ex-orphan-auto-cancel-123",
                    "client_order_id": "cl-orphan-123",
                    "market": "ETHINR",
                    "side": "buy",
                    "status": "open",
                    "price_per_unit": 250000.0,
                    "total_quantity": 0.01,
                }
            ],
        }
    )
    client.cancel_order = AsyncMock(
        return_value={"success": True, "result": {"status": "cancelled"}}
    )
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["orphan_orders"]) == 1
    assert res["orphan_orders"][0]["action"] == "FLAGGED_ORPHAN_ORDER"
    client.cancel_order.assert_not_called()


@pytest.mark.anyio
async def test_reconciliation_desynced_missing_balance(test_env):
    """Verifies OPEN position transitions to DESYNCED_MISSING_BALANCE when exchange balance is 0.0."""
    pos_repo = test_env["pos_repo"]
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    pos = Position(
        id="pos-desync-505",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=1.0,
        entry_price=12000.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        exchange_order_id="ex-sol-505",
    )
    await pos_repo.insert(pos)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "FILLED",
            "filled_qty": 1.0,
            "exchange_order_id": "ex-sol-505",
        }
    )
    # Asset balance on CoinDCX is 0.0!
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {"SOL": 0.0},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"
    assert len(res["desynced_positions"]) == 1
    assert res["desynced_positions"][0]["position_id"] == "pos-desync-505"
    assert (
        res["desynced_positions"][0]["action"]
        == "TRANSITIONED_TO_DESYNCED_MISSING_BALANCE"
    )

    # Verify status in SQLite repo
    updated = await pos_repo.get_by_id("pos-desync-505")
    assert (
        getattr(updated.status, "value", updated.status) == "DESYNCED_MISSING_BALANCE"
    )


@pytest.mark.anyio
async def test_reconciliation_handles_external_manual_exit(test_env):
    """Verifies position transitions to CLOSED with realized PnL on external manual exit."""
    pos_repo = test_env["pos_repo"]
    rec_service = test_env["rec_service"]
    mgr = test_env["mgr"]
    client = mgr.get_client(BotName.STE)

    pos = Position(
        id="pos-manual-exit-606",
        bot=BotName.STE,
        coin="XRP",
        pair="XRP/INR",
        qty=100.0,
        entry_price=50.0,
        entry_time=datetime.now(timezone.utc),
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        exchange_order_id="ex-xrp-606",
    )
    await pos_repo.insert(pos)

    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_order_status = AsyncMock(
        return_value={
            "success": True,
            "status": "MANUALLY_CLOSED",
            "avg_price": 60.0,
            "exchange_order_id": "ex-xrp-606",
        }
    )
    client.get_balances = AsyncMock(
        return_value={
            "success": True,
            "inr_balance": 10000.0,
            "inr_locked": 0.0,
            "asset_balances": {"XRP": 0.0},
        }
    )

    res = await rec_service.reconcile_positions()
    assert res["status"] == "DISCREPANCIES_DETECTED"

    # Verify position is closed in SQLite with calculated PnL ((60-50)*100 = 1000.0)
    updated = await pos_repo.get_by_id("pos-manual-exit-606")
    assert getattr(updated.status, "value", updated.status) == "CLOSED"
    assert updated.realized_pnl == 1000.0
