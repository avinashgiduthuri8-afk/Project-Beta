"""Unit tests for Step 3: SQLite 21-Table Database Schema Manager."""

import pytest
import sqlite3
from v2.storage.database import DatabaseManager


def test_21_tables_creation():
    db_mgr = DatabaseManager(":memory:")
    conn = db_mgr.get_connection()
    cursor = conn.cursor()

    # Fetch all table names in SQLite master
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row["name"] for row in cursor.fetchall()]

    expected_tables = [
        "signals", "positions", "trades", "market_candles", "ai_analyses",
        "strategy_calibrations", "event_log", "exchange", "data_vendor", "sector",
        "symbol", "daily_price", "intraday_price", "corporate_action", "bot_fleet_status",
        "risk_circuit_breakers", "portfolio_snapshots", "tax_friction_ledger",
        "walk_forward_metrics", "feedback_adjustments", "system_audit_log"
    ]

    for tbl in expected_tables:
        assert tbl in tables, f"Table '{tbl}' is missing from database schema!"

    assert len(tables) >= 21

