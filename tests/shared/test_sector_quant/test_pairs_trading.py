"""
Tests for sector_quant.strategies.pairs_trading
"""

from queue import Queue

from sector_quant.data.historic_sector import HistoricSectorDataHandler
from sector_quant.events import MarketEvent
from sector_quant.strategies.pairs_trading import PairsTradingStrategy


def test_pairs_trading_strategy():
    q = Queue()
    # Create synthetic series
    bars_y = [
        {
            "price_date": f"2026-01-{i:02d}",
            "open": 100 + i,
            "high": 105 + i,
            "low": 95 + i,
            "close": 100.0 + i * 2,
            "volume": 1000,
        }
        for i in range(1, 25)
    ]
    bars_x = [
        {
            "price_date": f"2026-01-{i:02d}",
            "open": 50 + i,
            "high": 55 + i,
            "low": 45 + i,
            "close": 50.0 + i * 1,
            "volume": 1000,
        }
        for i in range(1, 25)
    ]

    handler = HistoricSectorDataHandler(
        q, ["SYM_Y", "SYM_X"], {"SYM_Y": bars_y, "SYM_X": bars_x}
    )
    strat = PairsTradingStrategy(handler, q, ("SYM_Y", "SYM_X"), lookback_window=10)

    # Feed bars through
    for _ in range(15):
        handler.update_bars()
        while not q.empty():
            ev = q.get()
            if isinstance(ev, MarketEvent):
                strat.calculate_signals(ev)

    assert strat.sym_y == "SYM_Y"
    assert strat.sym_x == "SYM_X"
