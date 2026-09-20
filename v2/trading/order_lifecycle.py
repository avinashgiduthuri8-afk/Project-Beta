"""Order Lifecycle state machine and transition tracker matching Alpha architecture."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional, Set

logger = logging.getLogger(__name__)


class OrderLifecycleState(str, Enum):
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    RISK_APPROVED = "RISK_APPROVED"
    ORDER_CREATED = "ORDER_CREATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


# Allowed state transition map
ALLOWED_TRANSITIONS: Dict[OrderLifecycleState, Set[OrderLifecycleState]] = {
    OrderLifecycleState.SIGNAL_RECEIVED: {
        OrderLifecycleState.RISK_APPROVED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
    },
    OrderLifecycleState.RISK_APPROVED: {
        OrderLifecycleState.ORDER_CREATED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
    },
    OrderLifecycleState.ORDER_CREATED: {
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.FAILED,
        OrderLifecycleState.REJECTED,
    },
    OrderLifecycleState.SUBMITTED: {
        OrderLifecycleState.ACKNOWLEDGED,
        OrderLifecycleState.PENDING,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
    },
    OrderLifecycleState.ACKNOWLEDGED: {
        OrderLifecycleState.PENDING,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.FAILED,
    },
    OrderLifecycleState.PENDING: {
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
    },
    # Terminal states: no further transitions allowed
    OrderLifecycleState.FILLED: set(),
    OrderLifecycleState.REJECTED: set(),
    OrderLifecycleState.CANCELLED: set(),
    OrderLifecycleState.FAILED: set(),
}

TERMINAL_STATES = {
    OrderLifecycleState.FILLED,
    OrderLifecycleState.REJECTED,
    OrderLifecycleState.CANCELLED,
    OrderLifecycleState.FAILED,
}


class OrderLifecycleTracker:
    """Tracks and validates order state transitions and execution references."""

    def __init__(
        self,
        order_id: str,
        symbol: str,
        transaction_type: str,
        quantity: int,
        price: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ):
        self.order_id = order_id
        self.symbol = symbol.upper()
        self.transaction_type = transaction_type.upper()
        self.quantity = quantity
        self.price = price
        self.strategy_id = strategy_id

        self.broker_order_id: Optional[str] = None
        self.current_state: OrderLifecycleState = OrderLifecycleState.SIGNAL_RECEIVED
        self.history: List[Dict[str, Any]] = []
        self._record_transition(self.current_state, reason="Initial signal received")

    def _record_transition(self, new_state: OrderLifecycleState, reason: str = "") -> None:
        self.history.append({
            "state": new_state.value,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
        })

    def transition_to(self, new_state: OrderLifecycleState, reason: str = "", broker_order_id: Optional[str] = None) -> bool:
        """Transitions order to a new state if valid.

        Returns True if transition succeeded, False if invalid transition attempt.
        """
        if broker_order_id:
            self.broker_order_id = broker_order_id

        if self.current_state in TERMINAL_STATES:
            logger.warning(
                f"Invalid transition for order {self.order_id}: "
                f"Cannot transition from terminal state {self.current_state.value} to {new_state.value}"
            )
            return False

        allowed = ALLOWED_TRANSITIONS.get(self.current_state, set())
        if new_state not in allowed:
            logger.warning(
                f"Invalid state transition for order {self.order_id}: "
                f"{self.current_state.value} -> {new_state.value} is not permitted"
            )
            return False

        self.current_state = new_state
        self._record_transition(new_state, reason)
        logger.info(f"Order {self.order_id} ({self.symbol}) transitioned to {new_state.value} ({reason})")
        return True

    def is_terminal(self) -> bool:
        return self.current_state in TERMINAL_STATES

    def is_successful(self) -> bool:
        return self.current_state == OrderLifecycleState.FILLED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "transaction_type": self.transaction_type,
            "quantity": self.quantity,
            "price": self.price,
            "strategy_id": self.strategy_id,
            "current_state": self.current_state.value,
            "history": self.history,
        }

