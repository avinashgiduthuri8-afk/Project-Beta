"""
Unit tests for Indian Market Risk Management & Market Clock (Prompt C).
"""

from __future__ import annotations

from datetime import datetime
import pytz
from core.enums import Exchange, MarketSession, OrderSide, OrderType, ProductType
from core.models import AccountBalance, OrderRequest
from risk.market_clock import MarketClock
from risk.position_sizer import PositionSizer
from risk.risk_engine import RiskEngine

IST = pytz.timezone("Asia/Kolkata")


def test_market_clock_sessions():
    clock = MarketClock()

    # Pre-market: 09:05 IST on a weekday (e.g. Wednesday 2026-08-26)
    dt_pre = IST.localize(datetime(2026, 8, 26, 9, 5, 0))
    assert clock.get_session(dt_pre) == MarketSession.PRE_OPEN
    assert not clock.is_trading_allowed(dt_pre)

    # Trading: 10:30 IST
    dt_trade = IST.localize(datetime(2026, 8, 26, 10, 30, 0))
    assert clock.get_session(dt_trade) == MarketSession.TRADING
    assert clock.is_trading_allowed(dt_trade)

    # Auto-square-off: 15:20 IST
    dt_sq = IST.localize(datetime(2026, 8, 26, 15, 20, 0))
    assert clock.get_session(dt_sq) == MarketSession.AUTO_SQUARE_OFF
    assert clock.is_square_off_time(dt_sq)

    # Weekend
    dt_weekend = IST.localize(datetime(2026, 8, 29, 11, 0, 0))  # Saturday
    assert clock.get_session(dt_weekend) == MarketSession.WEEKEND


def test_position_sizer():
    # Capital ₹100,000, 1% risk = ₹1,000
    # Entry ₹2,000, Stop Loss ₹1,950 -> SL distance ₹50
    # Raw Qty = 1000 / 50 = 20 shares
    qty = PositionSizer.calculate_quantity_by_risk(
        capital=100000.0,
        risk_pct=1.0,
        entry_price=2000.0,
        stop_loss_price=1950.0,
        lot_size=1,
    )
    assert qty == 20

    # F&O NIFTY: Lot size 50
    # Raw qty = 1000 / 10 = 100 -> 2 lots of 50 = 100
    qty_fno = PositionSizer.calculate_quantity_by_risk(
        capital=100000.0,
        risk_pct=1.0,
        entry_price=150.0,
        stop_loss_price=140.0,
        lot_size=50,
    )
    assert qty_fno == 100


def test_circuit_breaker(risk_engine: RiskEngine):
    # Update P&L to breach daily limit of -₹5,000
    risk_engine.update_daily_pnl(realized_pnl=-5500.0, unrealized_pnl=0.0)
    assert risk_engine.is_circuit_breaker_active()

    # Any new order should be rejected
    req = OrderRequest(
        symbol="TCS",
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        product=ProductType.MIS,
        quantity=5,
        price=4200.0,
    )
    balance = AccountBalance(available_cash=100000.0, available_margin=100000.0)
    result = risk_engine.evaluate_order(req, balance)
    assert not result.allowed
    assert "Circuit Breaker is ACTIVE" in (result.reason or "")
