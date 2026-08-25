"""Unit tests for Risk Management Engine, Position Sizing, and Market Clock."""

from datetime import datetime
import pytest
from core.enums import MarketSession, OrderSide, OrderType, ProductType, Exchange
from core.models import OrderRequest, AccountBalance, Position
from risk.market_clock import MarketClock
from risk.position_sizer import PositionSizer
from risk.risk_engine import RiskEngine
from risk.rate_limiter import RateLimiter


def test_market_clock_sessions():
    clock = MarketClock()
    tz = clock.tz

    # 1. Normal Trading Hours: 10:30 IST on a Wednesday (2026-08-26)
    wed_10am = datetime(2026, 8, 26, 10, 30, 0, tzinfo=tz)
    assert clock.get_current_session(wed_10am) == MarketSession.NORMAL
    assert clock.is_normal_trading_active(wed_10am) is True

    # 2. Square-off Window: 15:20 IST
    wed_320pm = datetime(2026, 8, 26, 15, 20, 0, tzinfo=tz)
    assert clock.get_current_session(wed_320pm) == MarketSession.SQUARE_OFF_WINDOW
    assert clock.is_auto_square_off_time(wed_320pm) is True

    # 3. Weekend: Sunday
    sunday = datetime(2026, 8, 30, 10, 30, 0, tzinfo=tz)
    assert clock.get_current_session(sunday) == MarketSession.CLOSED


def test_position_sizer():
    # Capital = 100,000, Risk = 1% (₹1,000), Entry = 2000, SL = 1950 (Risk/share = ₹50)
    # Expected quantity = 1,000 / 50 = 20 shares
    qty = PositionSizer.calculate_quantity(
        capital=100000.0,
        risk_per_trade_pct=1.0,
        entry_price=2000.0,
        stop_loss_price=1950.0,
        lot_size=1,
    )
    assert qty == 20

    # NIFTY F&O lot sizing (lot_size = 50)
    # Entry = 24500, SL = 24450 (Risk/point = 50). Risk amount = 5,000 => 100 shares => 2 lots
    fno_qty = PositionSizer.calculate_quantity(
        capital=500000.0,
        risk_per_trade_pct=1.0,
        entry_price=24500.0,
        stop_loss_price=24450.0,
        lot_size=50,
        margin_pct=10.0,
    )
    assert fno_qty == 100


def test_circuit_breaker():
    clock = MarketClock()
    limiter = RateLimiter(rate=10.0)
    engine = RiskEngine(max_daily_loss=2000.0, rate_limiter=limiter, market_clock=clock)

    balance_ok = AccountBalance(total_capital=100000.0, available_margin=50000.0, realized_pnl=-500.0, unrealized_pnl=0.0)
    req = OrderRequest(
        client_order_id="RMS-01",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
    )

    # Simulated Loss beyond limit: -₹2500
    balance_loss = AccountBalance(total_capital=97500.0, available_margin=50000.0, realized_pnl=-2500.0, unrealized_pnl=0.0)
    allowed, msg = engine.validate_order(req, balance_loss, [])
    assert allowed is False
    assert "Daily max loss limit" in msg
