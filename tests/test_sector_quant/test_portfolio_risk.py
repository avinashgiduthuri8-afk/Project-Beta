"""Unit tests for Portfolio Management, Sector Risk Engine, and Position Sizing."""

import pytest
import pandas as pd
from datetime import datetime
from sector_quant.portfolio.risk_engine import SectorRiskEngine, RiskLimits
from sector_quant.portfolio.position_sizer import PositionSizer
from sector_quant.portfolio.metrics import PerformanceMetrics
from sector_quant.portfolio.portfolio import SectorPortfolio
from sector_quant.events.queue import EventQueue
from sector_quant.events.events import MarketEvent, SignalEvent, SignalType, FillEvent, OrderDirection
from sector_quant.data.historic_sector import HistoricSectorDataHandler


def test_sector_risk_engine_caps():
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

    # Test 1: Order for 100 shares @ $100 = $10,000 (10% of equity) -> Approved
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

    # Test 2: Order for 200 shares @ $100 = $20,000 (20% > max 15% stock cap) -> Adjusted to 150 shares
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
    assert qty == 150  # Capped at $15,000 // 100


def test_position_sizer_pairs():
    sizer = PositionSizer()
    equity = 100_000.0
    y_price = 100.0
    x_price = 50.0
    beta = 1.5

    q_y, q_x = sizer.calculate_pairs_quantities(
        equity=equity,
        y_price=y_price,
        x_price=x_price,
        beta=beta,
        total_pair_weight=0.20,  # $20,000 total
    )
    assert q_y > 0
    assert q_x > 0
    # Q_X should be ~ beta * Q_Y
    assert pytest.approx(q_x / q_y, rel=0.1) == 1.5


def test_performance_metrics():
    # Equity curve starting at $100k and ending at $110k with minor drawdown
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    eq_values = [100000, 101000, 102000, 100500, 103000, 105000, 104000, 107000, 108000, 110000]
    df = pd.DataFrame({"datetime": dates, "equity": eq_values})

    metrics = PerformanceMetrics.calculate_equity_metrics(df)
    assert pytest.approx(metrics["total_return_pct"], rel=1e-2) == 10.0
    assert metrics["sharpe_ratio"] > 0
    assert metrics["max_drawdown_pct"] < 0  # Drawdown is negative percentage

