"""
Tests for sector_quant.db.schema
"""

import sqlite3

from sector_quant.db.schema import (
    get_daily_prices,
    init_schema,
    insert_daily_price,
    insert_sector,
    insert_symbol,
)


def test_schema_init_and_crud():
    conn = sqlite3.connect(":memory:")
    try:
        init_schema(conn)

        # 1. Insert Sector
        sector_id = insert_sector(conn, "Banking", "NIFTY_BANK")
        assert sector_id > 0

        # Duplicate sector returns existing id
        sector_id_dup = insert_sector(conn, "Banking", "NIFTY_BANK")
        assert sector_id_dup == sector_id

        # 2. Insert Symbols
        hdbk_id = insert_symbol(conn, "HDFCBANK", "HDFC Bank Ltd", sector_id)
        assert hdbk_id > 0

        icici_id = insert_symbol(conn, "ICICIBANK", "ICICI Bank Ltd", sector_id)
        assert icici_id > 0

        # Duplicate symbol returns existing
        hdbk_dup = insert_symbol(conn, "HDFCBANK", "HDFC Bank Ltd", sector_id)
        assert hdbk_dup == hdbk_id

        # 3. Insert Prices
        insert_daily_price(
            conn, hdbk_id, "2026-01-01", 1600.0, 1620.0, 1590.0, 1610.0, 100000
        )
        insert_daily_price(
            conn, hdbk_id, "2026-01-02", 1610.0, 1630.0, 1605.0, 1625.0, 120000
        )

        # Query prices
        fetched = get_daily_prices(conn, hdbk_id)
        assert len(fetched) == 2
        assert fetched[0]["close"] == 1610.0
        assert fetched[1]["close"] == 1625.0
    finally:
        conn.close()
