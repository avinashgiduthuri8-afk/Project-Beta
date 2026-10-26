"""Priority 7B contracts for disabled, broker-neutral execution foundations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util

import pytest

from market.symbols import StockSymbol
from order_execution import (
    BrokerAdapter,
    BrokerOrderUpdate,
    ExecutionStatus,
    MockBroker,
    OrderIntent,
    OrderSide,
    validate_transition,
)
from order_execution.database import open_execution_database
from order_execution.store import ExecutionStore


NOW = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def remove_execution_database_artifacts(tmp_path):
    yield
    for path in tmp_path.glob("*.db*"):
        path.unlink(missing_ok=True)


def intent(
    *,
    intent_id: str = "intent-1",
    signal_id: str = "signal-1",
    correlation_id: str = "beta-abc123",
) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        signal_id=signal_id,
        correlation_id=correlation_id,
        symbol=StockSymbol("NSE", "TEST", "12345"),
        side=OrderSide.BUY,
        quantity=10,
        created_at=NOW,
    )


def update(
    status: ExecutionStatus,
    *,
    filled: int = 0,
    average_price: Decimal | None = None,
    occurred_at: datetime = NOW,
) -> BrokerOrderUpdate:
    return BrokerOrderUpdate(
        correlation_id="beta-abc123",
        broker_order_id="mock-order-1",
        status=status,
        requested_quantity=10,
        filled_quantity=filled,
        average_price=average_price,
        occurred_at=occurred_at,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"correlation_id": "bad/id"},
        {"correlation_id": "x" * 31},
        {"symbol": StockSymbol("NSE", "TEST", " ")},
        {"quantity": 0},
        {"created_at": datetime(2026, 10, 23)},
    ],
)
def test_order_intent_rejects_unsafe_identifiers_quantities_and_timestamps(overrides):
    values = {
        "intent_id": "intent-1",
        "signal_id": "signal-1",
        "correlation_id": "beta-abc123",
        "symbol": StockSymbol("NSE", "TEST", "12345"),
        "side": OrderSide.BUY,
        "quantity": 10,
        "created_at": NOW,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        OrderIntent(**values)


def test_state_machine_allows_partial_fill_path_and_rejects_terminal_transition():
    path = (
        ExecutionStatus.CREATED,
        ExecutionStatus.RISK_APPROVED,
        ExecutionStatus.SUBMITTING,
        ExecutionStatus.ACKNOWLEDGED,
        ExecutionStatus.PENDING,
        ExecutionStatus.PARTIALLY_FILLED,
        ExecutionStatus.FILLED,
    )
    for current, target in zip(path, path[1:]):
        validate_transition(current, target)

    with pytest.raises(ValueError, match="INVALID_EXECUTION_TRANSITION"):
        validate_transition(ExecutionStatus.FILLED, ExecutionStatus.PENDING)


@pytest.mark.asyncio
async def test_mock_broker_is_idempotent_and_models_partial_fills():
    broker: BrokerAdapter = MockBroker()
    order = intent()

    first = await broker.submit(order)
    duplicate = await broker.submit(order)
    partial = await broker.emit_update(
        order.correlation_id,
        ExecutionStatus.PARTIALLY_FILLED,
        filled_quantity=4,
        average_price=Decimal("100.25"),
        occurred_at=NOW + timedelta(seconds=1),
    )
    filled = await broker.emit_update(
        order.correlation_id,
        ExecutionStatus.FILLED,
        filled_quantity=10,
        average_price=Decimal("100.50"),
        occurred_at=NOW + timedelta(seconds=2),
    )

    assert first == duplicate
    assert first.status is ExecutionStatus.ACKNOWLEDGED
    assert broker.submission_count == 1
    assert partial.filled_quantity == 4
    assert filled.filled_quantity == 10
    assert await broker.get_order_by_correlation(order.correlation_id) == filled


@pytest.mark.asyncio
async def test_execution_database_must_not_reuse_beta_database_path(tmp_path):
    path = tmp_path / "shared.db"

    with pytest.raises(ValueError, match="EXECUTION_DATABASE_MUST_BE_SEPARATE"):
        await open_execution_database(str(path), beta_database_path=str(path))


@pytest.mark.asyncio
async def test_intent_creation_is_durable_and_duplicate_safe_across_restart(tmp_path):
    beta_path = tmp_path / "beta.db"
    execution_path = tmp_path / "execution.db"
    conn = await open_execution_database(
        str(execution_path), beta_database_path=str(beta_path)
    )
    store = ExecutionStore(conn)
    await store.initialize()
    assert await store.create_intent(intent()) is True
    assert await store.create_intent(intent()) is False
    assert await store.create_intent(
        intent(intent_id="intent-2", signal_id="signal-1", correlation_id="beta-other")
    ) is False
    await conn.close()

    reopened = await open_execution_database(
        str(execution_path), beta_database_path=str(beta_path)
    )
    try:
        restarted = ExecutionStore(reopened)
        stored = await restarted.get_intent("intent-1")
        events = await restarted.list_events("intent-1")
        async with reopened.execute(
            "SELECT version FROM execution_schema_version WHERE singleton=1"
        ) as cursor:
            schema_version = int((await cursor.fetchone())[0])
        async with reopened.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'beta_%'"
        ) as cursor:
            beta_tables = await cursor.fetchall()
        assert stored is not None
        assert stored["status"] == "CREATED"
        assert [event["status"] for event in events] == ["CREATED"]
        assert schema_version == 1
        assert beta_tables == []
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_store_persists_partial_fills_idempotently_and_rejects_regression(tmp_path):
    conn = await open_execution_database(
        str(tmp_path / "execution.db"), beta_database_path=str(tmp_path / "beta.db")
    )
    try:
        store = ExecutionStore(conn)
        await store.initialize()
        await store.create_intent(intent())
        with pytest.raises(ValueError, match="EXECUTION_TIMESTAMP_MUST_BE_TIMEZONE_AWARE"):
            await store.transition(
                "intent-1", ExecutionStatus.RISK_APPROVED, datetime(2026, 10, 23, 6, 0)
            )
        await store.transition("intent-1", ExecutionStatus.RISK_APPROVED, NOW)
        await store.transition("intent-1", ExecutionStatus.SUBMITTING, NOW)
        await store.apply_broker_update("intent-1", update(ExecutionStatus.ACKNOWLEDGED))
        partial = update(
            ExecutionStatus.PARTIALLY_FILLED,
            filled=4,
            average_price=Decimal("100.25"),
            occurred_at=NOW + timedelta(seconds=1),
        )

        assert await store.apply_broker_update("intent-1", partial) is True
        assert await store.apply_broker_update("intent-1", partial) is False
        with pytest.raises(ValueError, match="FILLED_QUANTITY_REGRESSION"):
            await store.apply_broker_update(
                "intent-1",
                update(
                    ExecutionStatus.PARTIALLY_FILLED,
                    filled=3,
                    average_price=Decimal("100.20"),
                    occurred_at=NOW + timedelta(seconds=2),
                ),
            )
        await store.apply_broker_update(
            "intent-1",
            update(
                ExecutionStatus.FILLED,
                filled=10,
                average_price=Decimal("100.50"),
                occurred_at=NOW + timedelta(seconds=3),
            ),
        )

        stored = await store.get_intent("intent-1")
        events = await store.list_events("intent-1")
        assert stored["status"] == "FILLED"
        assert stored["filled_quantity"] == 10
        assert [event["status"] for event in events] == [
            "CREATED", "RISK_APPROVED", "SUBMITTING", "ACKNOWLEDGED",
            "PARTIALLY_FILLED", "FILLED",
        ]
    finally:
        await conn.close()


def test_foundation_has_no_dhan_order_adapter_or_live_service():
    assert importlib.util.find_spec("order_execution.dhan") is None
    assert importlib.util.find_spec("services.live_bot") is None
