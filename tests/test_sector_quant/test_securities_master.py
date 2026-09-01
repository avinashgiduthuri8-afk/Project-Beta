"""Unit tests for Securities Master Database, Schemas, and Corporate Action Adjustments."""

import pytest
import pandas as pd
import numpy as np
from sector_quant.db.master import SecuritiesMaster
from sector_quant.db.schema import (
    ExchangeType,
    DataVendorType,
    SectorType,
    SymbolType,
)
from sector_quant.db.adjustments import calculate_corporate_action_adjustments
from sector_quant.db.ingestion import SectorDataIngestor


@pytest.fixture
def master_db():
    return SecuritiesMaster(":memory:")


def test_schema_and_default_entities(master_db):
    # Verify default seeded exchanges
    assert master_db.get_exchange_id("NYSE") is not None
    assert master_db.get_exchange_id("NASDAQ") is not None
    assert master_db.get_exchange_id("NSE") is not None

    # Verify default seeded sectors
    energy_sec = master_db.get_sector("ENERGY")
    assert energy_sec is not None
    assert energy_sec.code == "ENERGY"
    assert energy_sec.benchmark_symbol == "XLE"

    tech_sec = master_db.get_sector("TECH")
    assert tech_sec is not None
    assert tech_sec.benchmark_symbol == "XLK"

    sectors = master_db.list_sectors()
    assert len(sectors) >= 8


def test_symbol_registration_and_sector_constituents(master_db):
    sym_id = master_db.register_symbol(
        ticker="AREX",
        exchange_code="NYSE",
        sector_code="ENERGY",
        security_name="Approach Resources Inc.",
    )
    assert sym_id > 0

    sym = master_db.get_symbol("AREX")
    assert sym is not None
    assert sym.ticker == "AREX"

    master_db.register_symbol("WLL", "NYSE", "ENERGY", "Whiting Petroleum Corp.")
    constituents = master_db.get_sector_constituents("ENERGY")
    tickers = [c.ticker for c in constituents]
    assert "AREX" in tickers
    assert "WLL" in tickers


def test_daily_price_insertion_and_queries(master_db):
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    df = pd.DataFrame({
        "date": dates,
        "open": [100.0, 102.0, 101.0, 103.0, 105.0],
        "high": [103.0, 104.0, 103.0, 106.0, 107.0],
        "low": [99.0, 101.0, 100.0, 102.0, 104.0],
        "close": [102.0, 101.5, 102.8, 105.2, 106.0],
        "volume": [1000, 1200, 1100, 1500, 1400],
    })

    inserted = master_db.insert_daily_prices("XOM", df)
    assert inserted == 5

    retrieved = master_db.get_daily_prices("XOM", start_date="2024-01-01", end_date="2024-01-05")
    assert len(retrieved) == 5
    assert retrieved.iloc[0]["close"] == 102.0
    assert retrieved.iloc[-1]["close"] == 106.0


def test_corporate_action_split_adjustment():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "open": [200.0, 202.0, 204.0, 103.0, 105.0],
        "high": [205.0, 206.0, 207.0, 106.0, 107.0],
        "low": [198.0, 200.0, 202.0, 101.0, 104.0],
        "close": [202.0, 204.0, 206.0, 104.0, 106.0],
        "volume": [1000, 1000, 1000, 2000, 2000],
    })

    splits = [{"date": "2024-01-04", "ratio": 2.0}]
    adj_df = calculate_corporate_action_adjustments(df, splits=splits)

    assert adj_df.iloc[-1]["adj_factor"] == 1.0
    assert adj_df.iloc[-1]["adj_close"] == 106.0
    assert adj_df.iloc[0]["adj_factor"] == 0.5
    assert adj_df.iloc[0]["adj_close"] == 101.0
    assert adj_df.iloc[0]["adj_open"] == 100.0


def test_corporate_action_dividend_adjustment():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "open": [98.0, 99.0, 100.0, 96.0, 97.0],
        "high": [101.0, 101.0, 102.0, 98.0, 99.0],
        "low": [97.0, 98.0, 99.0, 95.0, 96.0],
        "close": [100.0, 100.0, 100.0, 96.0, 98.0],
        "volume": [1000, 1000, 1000, 1000, 1000],
    })

    dividends = [{"date": "2024-01-04", "dividend": 5.0}]
    adj_df = calculate_corporate_action_adjustments(df, dividends=dividends)

    assert adj_df.iloc[-1]["adj_factor"] == 1.0
    assert pytest.approx(adj_df.iloc[0]["adj_factor"], rel=1e-3) == 0.95
    assert pytest.approx(adj_df.iloc[0]["adj_close"], rel=1e-3) == 95.0


def test_sector_data_matrix_query(master_db):
    ingestor = SectorDataIngestor(master_db)
    ingestor.generate_synthetic_sector_data(
        sector_code="ENERGY",
        benchmark_ticker="XLE",
        constituent_tickers=["AREX", "WLL"],
        num_days=30,
        seed=42,
    )
    matrix = master_db.get_sector_prices_matrix("ENERGY")
    assert not matrix.empty
    assert "AREX" in matrix.columns
    assert "WLL" in matrix.columns
    assert len(matrix) == 30
