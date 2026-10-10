"""
V2 Order Repository.

Handles persistence, retrieval, and audit logging for live order lifecycle management.
"""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from core.repository.base import BaseRepository
from core.types import BotMode, BotName, Order, OrderState, OrderStateTransition


class OrderRepository(BaseRepository):
    """Repository for managing Order entity and OrderStateTransition audit records in SQLite."""

    def _row_to_order(self, row: aiosqlite.Row) -> Order:
        created_at_dt = (
            datetime.fromisoformat(row["created_at"])
            if isinstance(row["created_at"], str)
            else row["created_at"]
        )
        updated_at_dt = (
            datetime.fromisoformat(row["updated_at"])
            if isinstance(row["updated_at"], str)
            else row["updated_at"]
        )
        return Order(
            id=row["id"],
            client_order_id=row["client_order_id"],
            exchange_order_id=row["exchange_order_id"],
            bot=BotName(row["bot"]),
            coin=row["coin"],
            pair=row["pair"],
            side=row["side"],
            order_type=row["order_type"],
            req_qty=float(row["req_qty"]),
            price=float(row["price"]),
            filled_qty=float(row["filled_qty"]),
            remaining_qty=float(row["remaining_qty"]),
            avg_price=float(row["avg_price"]),
            state=OrderState(row["state"]),
            position_id=row["position_id"],
            signal_id=row["signal_id"],
            mode=BotMode(row["mode"]),
            created_at=created_at_dt,
            updated_at=updated_at_dt,
            error_message=row["error_message"],
        )

    def _row_to_transition(self, row: aiosqlite.Row) -> OrderStateTransition:
        ts_dt = (
            datetime.fromisoformat(row["timestamp"])
            if isinstance(row["timestamp"], str)
            else row["timestamp"]
        )
        meta = self._loads(row["metadata"]) if row["metadata"] else {}
        return OrderStateTransition(
            id=row["id"],
            order_id=row["order_id"],
            from_state=OrderState(row["from_state"]),
            to_state=OrderState(row["to_state"]),
            timestamp=ts_dt,
            reason=row["reason"],
            metadata=meta if isinstance(meta, dict) else {},
        )

    async def insert(self, order: Order) -> None:
        """Insert a new Order record into database."""
        sql = """
            INSERT INTO orders (
                id, client_order_id, exchange_order_id, bot, coin, pair, side, order_type,
                req_qty, price, filled_qty, remaining_qty, avg_price, state,
                position_id, signal_id, mode, created_at, updated_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            order.id,
            order.client_order_id,
            order.exchange_order_id,
            order.bot.value if hasattr(order.bot, "value") else str(order.bot),
            order.coin,
            order.pair,
            order.side,
            order.order_type,
            order.req_qty,
            order.price,
            order.filled_qty,
            order.remaining_qty,
            order.avg_price,
            order.state.value if hasattr(order.state, "value") else str(order.state),
            order.position_id,
            order.signal_id,
            order.mode.value if hasattr(order.mode, "value") else str(order.mode),
            order.created_at.isoformat(),
            order.updated_at.isoformat(),
            order.error_message,
        )
        await self._execute(sql, params)

    async def update(self, order: Order) -> None:
        """Update an existing Order record."""
        sql = """
            UPDATE orders SET
                exchange_order_id = ?,
                filled_qty = ?,
                remaining_qty = ?,
                avg_price = ?,
                state = ?,
                position_id = ?,
                updated_at = ?,
                error_message = ?
            WHERE id = ?
        """
        params = (
            order.exchange_order_id,
            order.filled_qty,
            order.remaining_qty,
            order.avg_price,
            order.state.value if hasattr(order.state, "value") else str(order.state),
            order.position_id,
            order.updated_at.isoformat(),
            order.error_message,
            order.id,
        )
        await self._execute(sql, params)

    async def record_transition(self, transition: OrderStateTransition) -> None:
        """Record an order state transition into audit log."""
        sql = """
            INSERT INTO order_state_transitions (
                id, order_id, from_state, to_state, timestamp, reason, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            transition.id,
            transition.order_id,
            (
                transition.from_state.value
                if hasattr(transition.from_state, "value")
                else str(transition.from_state)
            ),
            (
                transition.to_state.value
                if hasattr(transition.to_state, "value")
                else str(transition.to_state)
            ),
            transition.timestamp.isoformat(),
            transition.reason,
            self._dumps(transition.metadata or {}),
        )
        await self._execute(sql, params)

    async def get_by_id(self, order_id: str) -> Order | None:
        """Fetch order by internal order ID."""
        sql = "SELECT * FROM orders WHERE id = ?"
        row = await self._fetchone(sql, (order_id,))
        return self._row_to_order(row) if row else None

    async def get_by_client_order_id(self, client_order_id: str) -> Order | None:
        """Fetch order by client_order_id."""
        sql = "SELECT * FROM orders WHERE client_order_id = ?"
        row = await self._fetchone(sql, (client_order_id,))
        return self._row_to_order(row) if row else None

    async def get_by_exchange_order_id(self, exchange_order_id: str) -> Order | None:
        """Fetch order by exchange_order_id."""
        sql = "SELECT * FROM orders WHERE exchange_order_id = ?"
        row = await self._fetchone(sql, (exchange_order_id,))
        return self._row_to_order(row) if row else None

    async def get_active_orders(self) -> list[Order]:
        """Fetch all orders in active / non-terminal states."""
        active_states = (
            OrderState.CREATED.value,
            OrderState.SUBMITTED.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
            OrderState.UNKNOWN.value,
        )
        sql = f"SELECT * FROM orders WHERE state IN ({','.join(['?'] * len(active_states))})"
        rows = await self._fetchall(sql, active_states)
        return [self._row_to_order(r) for r in rows]

    async def get_transitions_for_order(
        self, order_id: str
    ) -> list[OrderStateTransition]:
        """Fetch all state transition audit logs for a specific order."""
        sql = "SELECT * FROM order_state_transitions WHERE order_id = ? ORDER BY timestamp ASC"
        rows = await self._fetchall(sql, (order_id,))
        return [self._row_to_transition(r) for r in rows]
