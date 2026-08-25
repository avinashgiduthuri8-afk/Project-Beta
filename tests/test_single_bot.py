"""Unit tests for the standalone Indian Stock Execution Bot."""

import pytest
from indian_stock_execution_bot import IndianStockExecutionBot, OrderSide, OrderType, ProductType, Exchange, ExecutionRouter, PositionSizer


def test_standalone_bot_execution():
    bot = IndianStockExecutionBot(broker_type="paper", initial_capital=100000.0, max_daily_loss=3000.0)
    bot.broker.slippage_pct = 0.0  # Zero slippage for exact price verification

    # 1. Test tick normalization
    assert ExecutionRouter.normalize_tick_size(2450.12, 0.05) == 2450.10
    assert ExecutionRouter.normalize_tick_size(2450.14, 0.05) == 2450.15

    # 2. Test position sizing
    qty = PositionSizer.calculate_quantity(
        capital=100000.0,
        risk_pct=1.0,
        entry=2000.0,
        sl=1950.0,
        lot_size=1,
    )
    assert qty == 20

    # 3. Direct trade execution
    req_order = bot.router.route_order(
        req=type('OrderReq', (), {
            'client_order_id': 'TEST-01',
            'symbol': 'RELIANCE',
            'side': OrderSide.BUY,
            'order_type': OrderType.MARKET,
            'quantity': 10,
            'price': 2850.0,
            'trigger_price': None,
            'product_type': ProductType.MIS,
            'exchange': Exchange.NSE,
        })()
    )
    assert req_order.symbol == "RELIANCE"
    assert req_order.filled_quantity == 10
    assert req_order.average_price == 2850.0
