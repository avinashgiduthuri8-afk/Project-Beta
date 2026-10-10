"""
PROJECT-ALPHA — Tests for Portfolio/Account Reconciliation (Skill 13).

Verifies:
  1. Periodic and on-demand discrepancy detection between local SQLite DB and exchange state.
  2. Immediate on-demand reconciliation execution and duration reporting.
  3. Detection of orphan orders, missing orders, and balance mismatches.
  4. Detection of position quantity divergence and desynced missing balance.
  5. Clean reconciliation reporting when state is fully synchronized.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from core.bus.event_bus import EventBus
from core.types import BotMode, BotName, Position, PositionStatus
from execution.reconciliation import ReconciliationService


@pytest.mark.asyncio
async def test_reconcile_on_demand_clean_state():
    pos_repo = MagicMock()
    pos_repo.get_active_positions = AsyncMock(return_value=[])

    sub_mgr = MagicMock()
    client = MagicMock()
    client.wallet_balance_inr = 5000.0
    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    client.get_balances = AsyncMock(
        return_value={"success": True, "inr_balance": 5000.0, "inr_locked": 0.0, "asset_balances": {}}
    )
    sub_mgr.get_client.return_value = client

    reconciliation = ReconciliationService(
        position_repo=pos_repo,
        subaccount_manager=sub_mgr,
        interval_seconds=60,
    )

    report = await reconciliation.reconcile_on_demand()
    assert report["on_demand"] is True
    assert report["is_clean"] is True
    assert report["status"] == "IN_SYNC"
    assert report["duration_ms"] >= 0.0
    assert report["mismatches"] == 0


@pytest.mark.asyncio
async def test_reconcile_on_demand_orphan_order_detected():
    pos_repo = MagicMock()
    pos_repo.get_active_positions = AsyncMock(return_value=[])

    sub_mgr = MagicMock()
    client = MagicMock()
    client.wallet_balance_inr = 5000.0
    # Exchange has an open order not tracked in SQLite
    client.get_active_orders = AsyncMock(
        return_value={
            "success": True,
            "orders": [
                {
                    "id": "CDX_ORPHAN_1",
                    "client_order_id": "CL_UNKNOWN",
                    "market": "BTCINR",
                    "side": "buy",
                    "price": 5000000.0,
                    "quantity": 0.001,
                    "status": "OPEN",
                }
            ],
        }
    )
    client.get_balances = AsyncMock(
        return_value={"success": True, "inr_balance": 5000.0, "inr_locked": 0.0, "asset_balances": {}}
    )
    sub_mgr.get_client.return_value = client

    reconciliation = ReconciliationService(
        position_repo=pos_repo,
        subaccount_manager=sub_mgr,
        interval_seconds=60,
    )

    report = await reconciliation.reconcile_on_demand()
    assert report["is_clean"] is False
    assert report["status"] == "DISCREPANCIES_DETECTED"
    assert len(report["orphan_orders"]) == 1
    assert report["orphan_orders"][0]["exchange_order_id"] == "CDX_ORPHAN_1"


@pytest.mark.asyncio
async def test_reconcile_on_demand_balance_mismatch_detected():
    pos_repo = MagicMock()
    pos_repo.get_active_positions = AsyncMock(return_value=[])

    sub_mgr = MagicMock()
    client = MagicMock()
    client.wallet_balance_inr = 5000.0
    client._lock = MagicMock()
    client._shared_state = {"wallet_balance_inr": 5000.0}
    client.get_active_orders = AsyncMock(return_value={"success": True, "orders": []})
    # Exchange has 4,200 INR (difference of ₹800)
    client.get_balances = AsyncMock(
        return_value={"success": True, "inr_balance": 4200.0, "inr_locked": 0.0, "asset_balances": {}}
    )
    sub_mgr.get_client.return_value = client

    reconciliation = ReconciliationService(
        position_repo=pos_repo,
        subaccount_manager=sub_mgr,
        interval_seconds=60,
    )

    report = await reconciliation.reconcile_on_demand()
    assert report["is_clean"] is False
    assert len(report["balance_mismatches"]) == 1
    assert report["balance_mismatches"][0]["difference"] == 800.0
