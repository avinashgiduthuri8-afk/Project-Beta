"""Unit tests for Step 1: Market Session Guard & Stock Broker Client Adapter."""

import pytest
from datetime import datetime, time, date
import pytz

from v2.core.market_session import MarketSessionGuard, MarketSession, IST_TZ
from v2.trading.stock_broker_client import StockBrokerClient, ProductType, TransactionType


def test_market_session_guard_trading_hours():
    guard = MarketSessionGuard(exchange="NSE")

    # Regular trading hours: Wednesday 10:30 AM IST
    dt_regular = IST_TZ.localize(datetime(2026, 1, 14, 10, 30))
    status = guard.get_session_status(check_dt=dt_regular)
    assert status == MarketSession.REGULAR

    # Pre-market: Wednesday 09:05 AM IST
    dt_pre = IST_TZ.localize(datetime(2026, 1, 14, 9, 5))
    status_pre = guard.get_session_status(check_dt=dt_pre)
    assert status_pre == MarketSession.PRE_MARKET

    # Square-off session: Wednesday 15:20 PM IST
    dt_sq = IST_TZ.localize(datetime(2026, 1, 14, 15, 20))
    status_sq = guard.get_session_status(check_dt=dt_sq)
    assert status_sq == MarketSession.SQUARE_OFF_ONLY

    # Closed hours: Wednesday 16:00 PM IST
    dt_closed = IST_TZ.localize(datetime(2026, 1, 14, 16, 0))
    status_closed = guard.get_session_status(check_dt=dt_closed)
    assert status_closed == MarketSession.CLOSED

    # Weekend check: Saturday
    dt_weekend = IST_TZ.localize(datetime(2026, 1, 17, 10, 30))
    assert guard.is_weekend(dt_weekend) is True
    assert guard.get_session_status(check_dt=dt_weekend) == MarketSession.CLOSED


def test_stock_broker_client_precision_and_order():
    client = StockBrokerClient(mode="PAPER", default_exchange="NSE")
    client.connect()

    # Test tick normalization (0.05 step for NSE)
    norm_price = client.normalize_tick_size(102.37, tick_size=0.05)
    assert norm_price == 102.35

    # Test integer quantity normalization
    norm_qty = client.normalize_quantity(15.8)
    assert norm_qty == 15

    # Test placing order asynchronously
    import asyncio

    async def run_order():
        resp = await client.place_order(
            symbol="RELIANCE",
            transaction_type="BUY",
            quantity=10,
            price=2450.50,
            product="MIS",
        )
        assert resp["order_id"].startswith("STK_ORD_")
        assert resp["symbol"] == "RELIANCE"
        assert resp["quantity"] == 10
        assert resp["status"] == "COMPLETE"

    asyncio.run(run_order())

