"""Transactional persistence for execution intents and broker updates."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import aiosqlite

from .contracts import (
    BrokerOrderUpdate,
    ExecutionStatus,
    OrderIntent,
    normalize_execution_timestamp,
    validate_transition,
)
from .database import initialize_execution_schema


class ExecutionStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def initialize(self) -> None:
        await initialize_execution_schema(self.conn)

    async def create_intent(self, intent: OrderIntent) -> bool:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            await self.conn.execute(
                """INSERT INTO execution_order_intents
                (intent_id,signal_id,correlation_id,canonical_symbol,exchange,security_id,
                 side,quantity,order_type,product_type,limit_price,trigger_price,status,
                 filled_quantity,average_price,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    intent.intent_id,
                    intent.signal_id,
                    intent.correlation_id,
                    intent.symbol.canonical,
                    intent.symbol.exchange,
                    intent.symbol.security_id,
                    intent.side.value,
                    intent.quantity,
                    intent.order_type.value,
                    intent.product_type.value,
                    str(intent.limit_price) if intent.limit_price is not None else None,
                    str(intent.trigger_price) if intent.trigger_price is not None else None,
                    ExecutionStatus.CREATED.value,
                    0,
                    None,
                    intent.created_at.isoformat(),
                    intent.created_at.isoformat(),
                ),
            )
            await self._insert_event(
                intent.intent_id,
                None,
                None,
                ExecutionStatus.CREATED,
                0,
                None,
                None,
                None,
                intent.created_at,
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            await self.conn.rollback()
            return False
        except Exception:
            await self.conn.rollback()
            raise

    async def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM execution_order_intents WHERE intent_id=?", (intent_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def transition(
        self, intent_id: str, target: ExecutionStatus, occurred_at: datetime
    ) -> bool:
        occurred_at = normalize_execution_timestamp(occurred_at)
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = await self._intent_row(intent_id)
            current = ExecutionStatus(row["status"])
            if current is target:
                await self.conn.rollback()
                return False
            validate_transition(current, target)
            await self.conn.execute(
                "UPDATE execution_order_intents SET status=?,updated_at=? WHERE intent_id=?",
                (target.value, occurred_at.isoformat(), intent_id),
            )
            await self._insert_event(
                intent_id, None, current, target, int(row["filled_quantity"]),
                Decimal(row["average_price"]) if row["average_price"] is not None else None,
                None, None, occurred_at,
            )
            await self.conn.commit()
            return True
        except Exception:
            await self.conn.rollback()
            raise

    async def apply_broker_update(
        self, intent_id: str, update: BrokerOrderUpdate
    ) -> bool:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = await self._intent_row(intent_id)
            if row["correlation_id"] != update.correlation_id:
                raise ValueError("BROKER_CORRELATION_MISMATCH")
            if int(row["quantity"]) != update.requested_quantity:
                raise ValueError("BROKER_QUANTITY_MISMATCH")
            current = ExecutionStatus(row["status"])
            current_filled = int(row["filled_quantity"])
            current_average = row["average_price"]
            average = str(update.average_price) if update.average_price is not None else None
            async with self.conn.execute(
                "SELECT broker_order_id FROM execution_broker_orders WHERE intent_id=?",
                (intent_id,),
            ) as cursor:
                broker_row = await cursor.fetchone()
            if broker_row and broker_row["broker_order_id"] != update.broker_order_id:
                raise ValueError("BROKER_ORDER_ID_MISMATCH")
            if update.filled_quantity < current_filled:
                raise ValueError("FILLED_QUANTITY_REGRESSION")
            if (
                current is update.status
                and current_filled == update.filled_quantity
                and current_average == average
            ):
                await self.conn.rollback()
                return False
            validate_transition(current, update.status)
            await self.conn.execute(
                """INSERT INTO execution_broker_orders
                (broker_order_id,intent_id,correlation_id,status,requested_quantity,
                 filled_quantity,average_price,error_code,error_message,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(intent_id) DO UPDATE SET
                status=excluded.status,filled_quantity=excluded.filled_quantity,
                average_price=excluded.average_price,error_code=excluded.error_code,
                error_message=excluded.error_message,updated_at=excluded.updated_at""",
                (
                    update.broker_order_id, intent_id, update.correlation_id,
                    update.status.value, update.requested_quantity, update.filled_quantity,
                    average, update.error_code, update.error_message,
                    update.occurred_at.isoformat(),
                ),
            )
            await self.conn.execute(
                """UPDATE execution_order_intents
                SET status=?,filled_quantity=?,average_price=?,updated_at=?
                WHERE intent_id=?""",
                (
                    update.status.value, update.filled_quantity, average,
                    update.occurred_at.isoformat(), intent_id,
                ),
            )
            await self._insert_event(
                intent_id, update.broker_order_id, current, update.status,
                update.filled_quantity, update.average_price, update.error_code,
                update.error_message, update.occurred_at,
            )
            await self.conn.commit()
            return True
        except Exception:
            await self.conn.rollback()
            raise

    async def list_events(self, intent_id: str) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM execution_order_events WHERE intent_id=? ORDER BY id",
            (intent_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def _intent_row(self, intent_id: str) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM execution_order_intents WHERE intent_id=?", (intent_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("EXECUTION_INTENT_NOT_FOUND")
        return row

    async def _insert_event(
        self,
        intent_id: str,
        broker_order_id: str | None,
        previous: ExecutionStatus | None,
        status: ExecutionStatus,
        filled_quantity: int,
        average_price: Decimal | None,
        error_code: str | None,
        error_message: str | None,
        occurred_at: datetime,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO execution_order_events
            (intent_id,broker_order_id,previous_status,status,filled_quantity,
             average_price,error_code,error_message,occurred_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                intent_id, broker_order_id, previous.value if previous else None,
                status.value, filled_quantity,
                str(average_price) if average_price is not None else None,
                error_code, error_message, occurred_at.isoformat(),
            ),
        )
