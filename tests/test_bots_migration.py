"""Unit tests for migrated MTB, MRB, and PMB execution bot engines."""

import pytest
from datetime import datetime, time
from core.models import Candle, Tick, Position, AccountBalance
from core.enums import OrderSide, OrderType, ProductType, Exchange
from brokers.paper_broker import PaperBroker
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from risk.market_clock import MarketClock
from risk.risk_engine import RiskEngine
from execution.mtb_bot import MomentumTradingBot
from execution.mrb_bot import MeanReversionBot
from execution.pmb_bot import PortfolioManagementBot


def test_mtb_momentum_breakout():
    broker = PaperBroker()
    router = ExecutionRouter(broker)
    om = OrderManager()
    mtb = MomentumTradingBot(router, om)

    # Bullish Breakout Candle: Open <= VWAP (2490 <= 2500) and Close > VWAP (2520 > 2500)
    c_bull = Candle(
        symbol="RELIANCE",
        timeframe_minutes=5,
        timestamp=datetime.now(),
        open=2490.0,
        high=2525.0,
        low=2485.0,
        close=2520.0,
        volume=1000,
        vwap=2500.0,
        is_closed=True,
    )
    order = mtb.on_candle(c_bull, capital=100000.0, lot_size=1)
    assert order is not None
    assert order.symbol == "RELIANCE"
    assert order.side == OrderSide.BUY
    assert "RELIANCE" in mtb.active_trades


def test_mrb_mean_reversion_fade():
    broker = PaperBroker()
    router = ExecutionRouter(broker)
    om = OrderManager()
    mrb = MeanReversionBot(router, om, deviation_threshold_pct=1.0)

    # Overbought Candle: Close (2530) is > 1% higher than VWAP (2500)
    c_overbought = Candle(
        symbol="INFY",
        timeframe_minutes=5,
        timestamp=datetime.now(),
        open=2510.0,
        high=2535.0,
        low=2505.0,
        close=2530.0,
        volume=500,
        vwap=2500.0,
        is_closed=True,
    )
    order = mrb.on_candle(c_overbought, capital=100000.0, lot_size=1)
    assert order is not None
    assert order.symbol == "INFY"
    assert order.side == OrderSide.SELL
    assert "INFY" in mrb.active_trades


def test_pmb_portfolio_health_and_circuit_breaker():
    broker = PaperBroker()
    router = ExecutionRouter(broker)
    om = OrderManager()
    clock = MarketClock()
    rms = RiskEngine(max_daily_loss=3000.0, market_clock=clock)
    pmb = PortfolioManagementBot(router, om, rms, market_clock=clock, max_daily_loss=3000.0)

    pos1 = Position(symbol="TCS", product_type=ProductType.MIS)
    pos1.quantity = 10
    pos1.average_buy_price = 4000.0
    pos1.ltp = 3700.0
    pos1.unrealized_pnl = -3000.0

    balance = AccountBalance(total_capital=97000.0, available_margin=50000.0, realized_pnl=-500.0, unrealized_pnl=-3000.0)
    
    # Check total loss (-3500) crosses max limit (3000)
    health = pmb.check_portfolio_health(balance, [pos1])
    assert health["status"] == "HALTED"
    assert health["action"] == "EMERGENCY_SHUTDOWN"
    assert pmb.circuit_breaker_active is True
