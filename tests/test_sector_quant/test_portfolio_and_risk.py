"""Unit tests for Portfolio Management, Sector Risk Limits, and Position Sizing."""

import pytest
import pandas as pd
from sector_quant.portfolio.risk_engine import SectorRiskEngine, RiskLimits
from sector_quant.portfolio.position_sizer import PositionSizer
from sector_quant.portfolio.metrics import PerformanceMetrics
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.events.queue import EventQueue
from sector_quant.events.events import SignalEvent, SignalType, OrderDirection, FillEvent


def test_sector_concentration_and_stock_caps():
    limits = RiskLimits(
        max_sector_allocation=0.30,  # Max $30,000 on $100k equity
        max_stock_allocation=0.15,   # Max $15,000
    )
    engine = SectorRiskEngine(limits=limits)

    equity = 100_000.0
    cash = 100_000.0
    positions = {}
    prices = {"XOM": 100.0}
    sec_map = {"XOM": "ENERGY"}

    # Order within limits
    approved, reason, qty = engine.validate_order(
        symbol="XOM",
        sector="ENERGY",
        order_direction="BUY",
        quantity=100,
        price=100.0,
        current_equity=equity,
        current_cash=cash,
        current_positions=positions,
        current_prices=prices,
        symbol_to_sector=sec_map,
    )
    assert approved is True
    assert qty == 100

    # Order exceeding stock cap (200 shares @ $100 = $20k > $15k limit) -> Adjusted to 150
    approved, reason, qty = engine.validate_order(
        symbol="XOM",
        sector="ENERGY",
        order_direction="BUY",
        quantity=200,
        price=100.0,
        current_equity=equity,
        current_cash=cash,
        current_positions=positions,
        current_prices=prices,
        symbol_to_sector=sec_map,
    )
    assert approved is True
    assert qty == 150


def test_position_sizer_hedge_ratio():
    sizer = PositionSizer()
    equity = 100_000.0
    y_price = 100.0
    x_price = 50.0
    beta = 1.35

    q_y, q_x = sizer.calculate_pairs_quantities(
        equity=equity,
        y_price=y_price,
        x_price=x_price,
        beta=beta,
        total_pair_weight=0.20,
    )
    assert q_y > 0
    assert q_x > 0
    assert pytest.approx(q_x / q_y, rel=0.1) == 1.35


def test_portfolio_mark_to_market_and_performance_math():
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    eq_values = [100000, 101000, 102000, 100500, 103000, 105000, 104000, 107000, 108000, 110000]
    df = pd.DataFrame({"datetime": dates, "equity": eq_values})

    metrics = PerformanceMetrics.calculate_equity_metrics(df)
    assert pytest.approx(metrics["total_return_pct"], rel=1e-2) == 10.0
    assert metrics["sharpe_ratio"] > 0
    assert metrics["sortino_ratio"] > 0
    assert metrics["max_drawdown_pct"] < 0

