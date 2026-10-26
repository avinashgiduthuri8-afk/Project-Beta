"""Broker interface and deterministic offline broker implementation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Protocol, runtime_checkable

from .contracts import BrokerOrderUpdate, ExecutionStatus, OrderIntent, validate_transition


@runtime_checkable
class BrokerAdapter(Protocol):
    async def submit(self, intent: OrderIntent) -> BrokerOrderUpdate: ...

    async def get_order_by_correlation(
        self, correlation_id: str
    ) -> BrokerOrderUpdate | None: ...


class MockBroker:
    """In-memory broker double; performs no I/O and never places real orders."""

    def __init__(self) -> None:
        self._intents: dict[str, OrderIntent] = {}
        self._updates: dict[str, BrokerOrderUpdate] = {}
        self.submission_count = 0

    async def submit(self, intent: OrderIntent) -> BrokerOrderUpdate:
        existing_intent = self._intents.get(intent.correlation_id)
        if existing_intent is not None:
            if existing_intent != intent:
                raise ValueError("CORRELATION_ID_CONFLICT")
            return self._updates[intent.correlation_id]
        broker_order_id = "mock-" + sha256(intent.correlation_id.encode()).hexdigest()[:16]
        update = BrokerOrderUpdate(
            correlation_id=intent.correlation_id,
            broker_order_id=broker_order_id,
            status=ExecutionStatus.ACKNOWLEDGED,
            requested_quantity=intent.quantity,
            filled_quantity=0,
            average_price=None,
            occurred_at=intent.created_at,
        )
        self._intents[intent.correlation_id] = intent
        self._updates[intent.correlation_id] = update
        self.submission_count += 1
        return update

    async def get_order_by_correlation(
        self, correlation_id: str
    ) -> BrokerOrderUpdate | None:
        return self._updates.get(correlation_id)

    async def emit_update(
        self,
        correlation_id: str,
        status: ExecutionStatus,
        *,
        filled_quantity: int,
        average_price: Decimal | None,
        occurred_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> BrokerOrderUpdate:
        intent = self._intents.get(correlation_id)
        current = self._updates.get(correlation_id)
        if intent is None or current is None:
            raise ValueError("MOCK_ORDER_NOT_FOUND")
        validate_transition(current.status, status)
        if filled_quantity < current.filled_quantity:
            raise ValueError("FILLED_QUANTITY_REGRESSION")
        update = BrokerOrderUpdate(
            correlation_id=correlation_id,
            broker_order_id=current.broker_order_id,
            status=status,
            requested_quantity=intent.quantity,
            filled_quantity=filled_quantity,
            average_price=average_price,
            occurred_at=occurred_at,
            error_code=error_code,
            error_message=error_message,
        )
        self._updates[correlation_id] = update
        return update

