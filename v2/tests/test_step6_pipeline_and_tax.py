"""Unit tests for Step 6: 14-Stage Execution Pipeline and Equity Tax Ledger."""

import pytest
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, time
import pytz

from v2.analytics.tax_ledger import EquityTaxLedger
from v2.core.pipeline_orchestrator import ExecutionPipelineOrchestrator
from v2.core.market_session import MarketSessionGuard, IST_TZ


def test_equity_tax_ledger_calculation():
    ledger = EquityTaxLedger()

    # Intraday MIS Trade on RELIANCE: Buy 100 shares @ ₹2400, Sell @ ₹2450 (Gross P&L = +₹5000)
    res_mis = ledger.calculate_trade_friction(
        symbol="RELIANCE",
        entry_price=2400.0,
        exit_price=2450.0,
        quantity=100,
        product="MIS",
    )
    assert res_mis["gross_pnl"] == 5000.0
    assert res_mis["net_pnl"] < 5000.0
    assert res_mis["total_friction"] > 0
    assert res_mis["breakdown"]["brokerage"] <= 40.0  # ₹20 max per leg * 2


def test_full_14_stage_pipeline_execution():
    orchestrator = ExecutionPipelineOrchestrator()

    # Mock market guard to simulate open regular session
    regular_dt = IST_TZ.localize(datetime(2026, 1, 14, 11, 0))
    orchestrator.market_guard.get_current_time = lambda: regular_dt

    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    prices = np.linspace(2400.0, 2450.0, 30)
    df = pd.DataFrame({"date": dates, "open": prices, "high": prices, "low": prices, "close": prices, "volume": [10000]*30})

    async def run_pipeline():
        trace = await orchestrator.execute_pipeline_cycle(
            symbol="RELIANCE",
            df=df,
            chart_score=90.0,
            indicator_score=85.0,
            regime_score=85.0,
            sentiment_score=80.0,
        )
        assert trace["final_status"] == "SUCCESSFULLY_EXECUTED_STAGES_1_TO_14"
        assert trace["stage_3_confluence"]["is_elite"] is True
        assert trace["stage_6_rms"]["approved"] is True
        assert trace["stage_10_tax_ledger"]["net_pnl"] > 0

    asyncio.run(run_pipeline())

