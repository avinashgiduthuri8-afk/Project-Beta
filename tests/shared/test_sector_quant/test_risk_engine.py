"""
Tests for sector_quant.portfolio.risk_engine
"""

from datetime import datetime, timezone

from sector_quant.events import FillEvent, SignalEvent
from sector_quant.portfolio.risk_engine import SectorRiskEngine


def test_sector_risk_engine_caps_and_sizing():
    sym_map = {
        "HDFCBANK": "BANKING",
        "ICICIBANK": "BANKING",
        "TCS": "IT",
    }
    engine = SectorRiskEngine(
        initial_capital=1_000_000.0,
        max_sector_exposure_pct=0.30,
        max_stock_exposure_pct=0.15,
        symbol_sector_map=sym_map,
    )

    assert engine.get_portfolio_equity() == 1_000_000.0

    # Update prices
    engine.update_price("HDFCBANK", 1500.0)
    engine.update_price("ICICIBANK", 1000.0)

    # Signal 1: Buy HDFCBANK (target 10% exposure = 100,000 INR -> ~66 shares)
    sig = SignalEvent("HDFCBANK", datetime.now(timezone.utc), "LONG", 1.0)
    order = engine.evaluate_order(sig, 1500.0)
    assert order is not None
    assert order.direction == "BUY"
    assert order.quantity > 0

    # Simulate fill
    fill = FillEvent(
        datetime.now(timezone.utc),
        "HDFCBANK",
        "NSE",
        order.quantity,
        "BUY",
        order.quantity * 1500.0,
        50.0,
    )
    engine.update_fill(fill)

    assert engine.positions["HDFCBANK"] == order.quantity
    assert engine.get_stock_exposure("HDFCBANK") > 0
    assert engine.get_sector_exposure("BANKING") > 0
