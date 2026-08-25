"""
Unit tests for OMS & Execution Router (Prompt B).
"""

from __future__ import annotations

from core.enums import Exchange, OrderSide, OrderStatus, OrderType, ProductType
from core.models import OrderRequest
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager


def test_tick_size_normalization():
    # NSE/BSE standard tick size is 0.05
    assert ExecutionRouter.normalize_tick_price(100.03, 0.05) == 100.05
    assert ExecutionRouter.normalize_tick_price(100.02, 0.05) == 100.00
    assert ExecutionRouter.normalize_tick_price(100.07, 0.05) == 100.05
    assert ExecutionRouter.normalize_tick_price(100.08, 0.05) == 100.10
    assert ExecutionRouter.normalize_tick_price(None, 0.05) is None


def test_lot_size_normalization():
    # NIFTY lot size 50
    assert ExecutionRouter.normalize_quantity(49, 50) == 50
    assert ExecutionRouter.normalize_quantity(75, 50) == 50
    assert ExecutionRouter.normalize_quantity(100, 50) == 100
    assert ExecutionRouter.normalize_quantity(120, 50) == 100
    # Equity single lot
    assert ExecutionRouter.normalize_quantity(17, 1) == 17


def test_order_manager_submission_and_tracking(order_manager: OrderManager):
    req = OrderRequest(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        product=ProductType.MIS,
        quantity=10,
        price=2950.0,
    )

    order = order_manager.submit_order(req, lot_size=1, tick_size=0.05)
    assert order.order_id is not None
    assert order.symbol == "RELIANCE"
    assert order.quantity == 10

    # Retrieve from manager
    stored = order_manager.get_order(order.order_id)
    assert stored is not None
    assert stored.order_id == order.order_id
