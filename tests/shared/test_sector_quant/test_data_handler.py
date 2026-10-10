"""
Tests for sector_quant.data.historic_sector
"""

from queue import Queue

from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.events import MarketEvent


def test_historic_sector_data_handler():
    q = Queue()
    data_feed = {
        "HDFCBANK": [
            {
                "price_date": "2026-01-01",
                "open": 1600.0,
                "high": 1620.0,
                "low": 1590.0,
                "close": 1610.0,
                "volume": 1000,
            },
            {
                "price_date": "2026-01-02",
                "open": 1610.0,
                "high": 1630.0,
                "low": 1600.0,
                "close": 1625.0,
                "volume": 1200,
            },
        ],
        "ICICIBANK": [
            {
                "price_date": "2026-01-01",
                "open": 1000.0,
                "high": 1020.0,
                "low": 990.0,
                "close": 1010.0,
                "volume": 2000,
            },
            {
                "price_date": "2026-01-02",
                "open": 1010.0,
                "high": 1030.0,
                "low": 1005.0,
                "close": 1020.0,
                "volume": 2200,
            },
        ],
    }

    handler = HistoricSectorDataHandler(q, ["HDFCBANK", "ICICIBANK"], data_feed)

    assert handler.continue_backtest is True
    assert handler.get_latest_bar("HDFCBANK") is None

    # Drip 1st bar (2026-01-01)
    handler.update_bars()
    assert handler.get_latest_bar("HDFCBANK")["close"] == 1610.0
    assert handler.get_latest_bar("ICICIBANK")["close"] == 1010.0
    assert not q.empty()
    event = q.get()
    assert isinstance(event, MarketEvent)

    # Drip 2nd bar (2026-01-02)
    handler.update_bars()
    assert handler.get_latest_bar("HDFCBANK")["close"] == 1625.0
    assert handler.get_latest_bar("ICICIBANK")["close"] == 1020.0

    # Next update reaches end
    handler.update_bars()
    assert handler.continue_backtest is False
