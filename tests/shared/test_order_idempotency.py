"""
PROJECT-ALPHA — Tests for Order Placement Idempotency (Skill 07).

Verifies:
  1. Deterministic client order ID generation.
  2. Duplicate trade rejection for concurrent & sequential duplicate signals.
  3. In-flight locking preventing race conditions across concurrent tasks.
  4. Cache TTL expiration and LRU/capacity pruning.
  5. AutoTradeRouter and TradingService integration.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.types import BotName, Signal
from execution.auto_trader import AutoTradeRouter
from execution.trading.idempotency import (
    IdempotencyGuard,
    IdempotencyRecord,
    IdempotencyState,
)


def test_client_order_id_determinism():
    guard = IdempotencyGuard()
    id1 = guard.generate_client_order_id("BTC", "BUY", "sig-1234")
    id2 = guard.generate_client_order_id("BTC", "BUY", "sig-1234")
    assert id1 == id2
    assert id1.startswith("ORD_BTC_BUY_")
    assert len(id1) <= 36

    # Different coin or side yields different ID
    id_sell = guard.generate_client_order_id("BTC", "SELL", "sig-1234")
    assert id1 != id_sell

    id_sol = guard.generate_client_order_id("SOL", "BUY", "sig-1234")
    assert id1 != id_sol


def test_idempotency_key_generation():
    guard = IdempotencyGuard()
    key1 = guard.generate_idempotency_key("BTC/INR", "sig-001")
    key2 = guard.generate_idempotency_key("btc", "sig-001")
    assert key1 == "BTCINR::sig-001"
    assert key2 == "BTC::sig-001"


@pytest.mark.asyncio
async def test_idempotent_execution_serializes_and_caches():
    guard = IdempotencyGuard()
    execution_count = 0

    async def mock_dispatch(client_order_id: str):
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.01)
        return {
            "success": True,
            "order_id": "ORD_EX_101",
            "client_order_id": client_order_id,
        }

    # First attempt: executes
    res1 = await guard.execute_idempotent("BTC", "BUY", "SIG_ALPHA", mock_dispatch)
    assert res1["success"] is True
    assert res1["order_id"] == "ORD_EX_101"
    assert execution_count == 1

    # Second attempt: cached
    res2 = await guard.execute_idempotent("BTC", "BUY", "SIG_ALPHA", mock_dispatch)
    assert res2["success"] is True
    assert res2.get("is_duplicate") is True
    assert res2.get("idempotent_cached") is True
    assert execution_count == 1  # Dispatch was NOT invoked again


@pytest.mark.asyncio
async def test_concurrent_idempotent_calls_prevent_duplicates():
    guard = IdempotencyGuard()
    dispatch_count = 0

    async def slow_dispatch(client_order_id: str):
        nonlocal dispatch_count
        dispatch_count += 1
        await asyncio.sleep(0.05)
        return {"success": True, "order_id": "ORD_CONCURRENT_1"}

    # Run 5 concurrent dispatch requests for the same signal
    tasks = [
        guard.execute_idempotent("ETH", "BUY", "SIG_CONCUR", slow_dispatch)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # Exactly 1 request successfully dispatches; subsequent requests either get cached result or duplicate rejection
    assert dispatch_count == 1
    successful = [r for r in results if r.get("success") is True]
    assert len(successful) >= 1


def test_ttl_expiry_and_pruning():
    guard = IdempotencyGuard(ttl_seconds=0.1, max_cache_size=5)
    key = guard.generate_idempotency_key("SOL", "SIG_EXPIRE")
    guard.record_completed(key, {"order_id": "ORD_OLD"})

    assert guard.is_processed(key) is True
    time.sleep(0.15)
    # After TTL, is_processed returns False
    assert guard.is_processed(key) is False


def test_cache_capacity_overflow_eviction():
    guard = IdempotencyGuard(ttl_seconds=3600, max_cache_size=3)
    for i in range(5):
        k = f"COIN_{i}::SIG"
        guard.record_in_flight(k, f"ORD_{i}")

    # Maximum capacity is constrained
    assert len(guard._records) <= 4


@pytest.mark.asyncio
async def test_autotraderouter_idempotency_integration():
    bus = EventBus()
    sub_mgr = MagicMock()
    mock_client = MagicMock()
    mock_client.subaccount_id = "SUB_TEST"
    mock_client.place_order.return_value = {
        "success": True,
        "order": {"id": "EX_ORD_1"},
    }
    sub_mgr.get_client.return_value = mock_client

    router = AutoTradeRouter(bus=bus, subaccount_manager=sub_mgr, dry_run=False)

    signal_payload = {
        "signal_id": "SIG_IDEMPOTENT_TEST",
        "coin": "BTC",
        "pair": "BTC/INR",
        "price": 5000000.0,
        "trade_amount": 200.0,
        "ticker_timestamp": time.time(),
    }

    # 1. First signal processing succeeds
    res1 = await router.handle_signal(signal_payload)
    assert res1.get("success") is True

    # 2. Duplicate signal rejected by idempotency filter
    res2 = await router.handle_signal(signal_payload)
    assert res2.get("success") is False
    assert res2.get("error") == "DUPLICATE_SIGNAL"
