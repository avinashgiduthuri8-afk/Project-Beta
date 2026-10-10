"""
PROJECT-ALPHA Live Order Lifecycle State Machine.

Enforces valid state transition paths, quantity/price updates, idempotency,
and generates audit trail records for order state transitions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from core.exceptions import AlphaError
from core.logging import get_logger
from core.types import Order, OrderState, OrderStateTransition

logger = get_logger("execution.trading.order_state_machine")


class InvalidOrderStateTransitionError(AlphaError):
    """Raised when an illegal order state transition is attempted."""


# Define valid state transition graph
VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.CREATED: {
        OrderState.SUBMITTED,
        OrderState.FAILED,
        OrderState.CANCELLED,
    },
    OrderState.SUBMITTED: {
        OrderState.OPEN,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.FAILED,
        OrderState.UNKNOWN,
    },
    OrderState.OPEN: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.FAILED,
        OrderState.UNKNOWN,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.FAILED,
        OrderState.UNKNOWN,
    },
    OrderState.UNKNOWN: {
        OrderState.OPEN,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.FAILED,
    },
    # Terminal states (only idempotent self-transitions allowed)
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.FAILED: set(),
}


class OrderStateMachine:
    """
    Live Order Lifecycle State Machine.
    """

    @staticmethod
    def can_transition(from_state: OrderState, to_state: OrderState) -> bool:
        """Check if transition from from_state to to_state is valid."""
        if from_state == to_state:
            return True  # Idempotent re-application
        valid_next_states = VALID_TRANSITIONS.get(from_state, set())
        return to_state in valid_next_states

    @classmethod
    def transition(
        cls,
        order: Order,
        to_state: OrderState,
        filled_qty: float | None = None,
        avg_price: float | None = None,
        exchange_order_id: str | None = None,
        reason: str | None = None,
        metadata: dict | None = None,
    ) -> tuple[Order, OrderStateTransition]:
        """
        Execute state transition on Order entity.

        Returns updated Order and OrderStateTransition record for persistence.
        Raises InvalidOrderStateTransitionError if transition is illegal.
        """
        from_state = order.state

        if not cls.can_transition(from_state, to_state):
            err_msg = (
                f"Invalid order state transition for order {order.id} "
                f"({order.coin}): cannot transition from {from_state.value} to {to_state.value}"
            )
            logger.error(err_msg)
            raise InvalidOrderStateTransitionError(err_msg)

        now = datetime.now(timezone.utc)

        # Update order properties
        order.state = to_state
        order.updated_at = now

        if exchange_order_id is not None:
            order.exchange_order_id = exchange_order_id

        if filled_qty is not None:
            order.filled_qty = max(0.0, float(filled_qty))
            order.remaining_qty = max(0.0, order.req_qty - order.filled_qty)

        if avg_price is not None:
            order.avg_price = float(avg_price)

        if to_state in (OrderState.REJECTED, OrderState.FAILED) and reason:
            order.error_message = reason

        # Create audit transition record
        transition_record = OrderStateTransition(
            id=f"TR-{uuid.uuid4().hex[:12]}",
            order_id=order.id,
            from_state=from_state,
            to_state=to_state,
            timestamp=now,
            reason=reason,
            metadata=metadata or {},
        )

        logger.info(
            "Order %s (%s %s) state transition: %s -> %s (filled: %s/%s, avg_px: %s)",
            order.id,
            order.side,
            order.pair,
            from_state.value,
            to_state.value,
            order.filled_qty,
            order.req_qty,
            order.avg_price,
        )

        return order, transition_record
