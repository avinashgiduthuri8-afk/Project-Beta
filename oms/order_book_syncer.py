"""Order Book Syncer to reconcile local state with broker order book."""

from __future__ import annotations

import logging
from typing import Optional
from core.interfaces import BaseBroker
from oms.order_manager import OrderManager

logger = logging.getLogger(__name__)


class OrderBookSyncer:
    """Periodically fetches broker order book and reconciles in-memory OMS state."""

    def __init__(self, broker: BaseBroker, order_manager: OrderManager):
        self.broker = broker
        self.order_manager = order_manager

    def sync(self) -> int:
        """Sync order book from broker. Returns number of updated orders."""
        try:
            broker_orders = self.broker.get_orders()
            updated_count = 0

            for b_order in broker_orders:
                local_order = self.order_manager.get_order(b_order.order_id)
                if local_order:
                    if local_order.status != b_order.status:
                        self.order_manager.update_order_status(
                            order_id=b_order.order_id,
                            new_status=b_order.status,
                            filled_qty=b_order.filled_quantity,
                            avg_price=b_order.average_price,
                            message=b_order.status_message,
                        )
                        updated_count += 1
                else:
                    self.order_manager.register_order(b_order)
                    updated_count += 1

            return updated_count
        except Exception as e:
            logger.error(f"Error synchronizing order book: {e}")
            return 0
