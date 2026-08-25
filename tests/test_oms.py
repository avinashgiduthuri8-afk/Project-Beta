"""Unit tests for OMS and Execution Router."""

import pytest
from core.enums import OrderSide, OrderType, ProductType, Exchange, OrderStatus
from core.models import OrderRequest
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager


def test_tick_size_normalization():
    # NSE ₹0.05 tick size rounding tests
    assert ExecutionRouter.normalize_tick_size(100.03, 0.05) == 100.05
    assert ExecutionRouter.normalize_tick_size(100.01, 0.05) == 100.00
    assert ExecutionRouter.normalize_tick_size(2450.12, 0.05) == 2450.10
    assert ExecutionRouter.normalize_tick_size(2450.14, 0.05) == 2450.15


def test_lot_size_normalization():
    # NIFTY lot size 50 tests
    assert ExecutionRouter.normalize_lot_size(40, lot_size=50) == 50
    assert ExecutionRouter.normalize_lot_size(75, lot_size=50) == 100
    assert ExecutionRouter.normalize_lot_size(10, lot_size=1) == 10


def test_order_manager_submission_and_tracking(order_manager, paper_broker, execution_router):
    req = OrderRequest(
        client_order_id="TEST-ORD-01",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.MIS,
        quantity=10,
    )
    order = execution_router.route_order(req)
    order_manager.register_order(order)

    tracked_order = order_manager.get_order(order.order_id)
    assert tracked_order is not None
    assert tracked_order.symbol == "RELIANCE"
    assert tracked_order.quantity == 10
    assert tracked_order.status == OrderStatus.COMPLETE
