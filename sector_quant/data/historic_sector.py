"""Historic Sector Data Handler for synchronized multi-symbol bar drip-feeding."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from sector_quant.data.base import DataHandler
from sector_quant.events.events import MarketEvent
from sector_quant.events.queue import EventQueue
from sector_quant.db.master import SecuritiesMaster
from sector_quant.db.adjustments import adjust_ohlcv_dataframe

logger = logging.getLogger(__name__)


class HistoricSectorDataHandler(DataHandler):
    """Event-driven historic data handler for synchronized sector multi-ticker universes."""

    def __init__(
        self,
        events_queue: EventQueue,
        symbol_list: Optional[List[str]] = None,
        master: Optional[SecuritiesMaster] = None,
        sector_code: Optional[str] = None,
        data_dict: Optional[Dict[str, pd.DataFrame]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjusted: bool = True,
    ):
        symbols = [s.upper() for s in (symbol_list or [])]
        self.master = master
        self.sector_code = sector_code
        self.start_date = start_date
        self.end_date = end_date
        self.adjusted = adjusted

        # If sector_code provided without symbol_list, fetch constituents from master
        if sector_code and not symbols and master:
            constituents = master.get_sector_constituents(sector_code)
            symbols = [c.ticker for c in constituents]
            sec = master.get_sector(sector_code)
            if sec and sec.benchmark_symbol and sec.benchmark_symbol not in symbols:
                symbols.append(sec.benchmark_symbol)

        super().__init__(events_queue=events_queue, symbol_list=symbols)

        self.symbol_data: Dict[str, pd.DataFrame] = {}
        self.latest_symbol_data: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.symbol_list}
        self.bar_index = 0
        self.all_dates: List[datetime] = []

        self._load_data(data_dict)

    def _load_data(self, data_dict: Optional[Dict[str, pd.DataFrame]]) -> None:
        """Loads and aligns multi-symbol historical bars to unified datetime timeline."""
        raw_dfs: Dict[str, pd.DataFrame] = {}

        if data_dict is not None:
            for s, df in data_dict.items():
                s_up = s.upper()
                if s_up not in self.symbol_list:
                    self.symbol_list.append(s_up)
                    self.latest_symbol_data[s_up] = []
                cleaned = df.copy()
                if not isinstance(cleaned.index, pd.DatetimeIndex):
                    if "date" in cleaned.columns:
                        cleaned["date"] = pd.to_datetime(cleaned["date"])
                        cleaned.set_index("date", inplace=True)
                    elif "price_date" in cleaned.columns:
                        cleaned["date"] = pd.to_datetime(cleaned["price_date"])
                        cleaned.set_index("date", inplace=True)
                raw_dfs[s_up] = adjust_ohlcv_dataframe(cleaned)
        elif self.master is not None:
            for s in self.symbol_list:
                df = self.master.get_daily_prices(
                    ticker=s,
                    start_date=self.start_date,
                    end_date=self.end_date,
                    adjusted=self.adjusted,
                )
                if not df.empty:
                    raw_dfs[s] = df
                else:
                    logger.warning(f"No price data found in Securities Master for symbol '{s}'")

        if not raw_dfs:
            logger.warning("HistoricSectorDataHandler loaded empty dataset.")
            self.continue_backtest = False
            return

        # Find intersecting dates across all loaded symbols
        date_sets = [set(df.index) for df in raw_dfs.values() if not df.empty]
        if not date_sets:
            self.continue_backtest = False
            return

        common_dates = sorted(list(set.intersection(*date_sets)))
        if not common_dates:
            # Fallback to sorted union if intersection is empty
            common_dates = sorted(list(set.union(*date_sets)))

        self.all_dates = common_dates

        # Reindex and align all symbols to common dates
        for sym in self.symbol_list:
            if sym in raw_dfs:
                aligned = raw_dfs[sym].reindex(self.all_dates).ffill().bfill()
                self.symbol_data[sym] = aligned
            else:
                self.symbol_data[sym] = pd.DataFrame()

        self.bar_index = 0
        self.continue_backtest = len(self.all_dates) > 0

    def update_bars(self) -> bool:
        """Pushes the next bar for each symbol into latest_symbol_data and emits MarketEvent."""
        if self.bar_index >= len(self.all_dates):
            self.continue_backtest = False
            return False

        current_dt = self.all_dates[self.bar_index]
        for sym in self.symbol_list:
            df = self.symbol_data.get(sym)
            if df is not None and not df.empty and current_dt in df.index:
                row = df.loc[current_dt]
                bar_dict = {
                    "symbol": sym,
                    "datetime": current_dt,
                    "open": float(row.get("open", row.get("open_price", 0.0))),
                    "high": float(row.get("high", row.get("high_price", 0.0))),
                    "low": float(row.get("low", row.get("low_price", 0.0))),
                    "close": float(row.get("close", row.get("close_price", 0.0))),
                    "adj_close": float(row.get("adj_close", row.get("adj_close_price", row.get("close", 0.0)))),
                    "volume": int(row.get("volume", 0)),
                }
                self.latest_symbol_data[sym].append(bar_dict)

        self.bar_index += 1
        # Emit a unified MarketEvent representing the new time index heartbeat
        self.events_queue.put(MarketEvent(datetime=current_dt))
        return True

    def get_latest_bar(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        bars = self.latest_symbol_data.get(sym, [])
        return bars[-1] if bars else None

    def get_latest_bars(self, symbol: str, N: int = 1) -> List[Dict[str, Any]]:
        sym = symbol.upper()
        bars = self.latest_symbol_data.get(sym, [])
        return bars[-N:] if bars else []

    def get_latest_bar_datetime(self, symbol: str) -> Optional[datetime]:
        bar = self.get_latest_bar(symbol)
        return bar["datetime"] if bar else None

    def get_latest_bar_value(self, symbol: str, val_type: str = "close") -> Optional[float]:
        bar = self.get_latest_bar(symbol)
        if not bar:
            return None
        return bar.get(val_type.lower())

    def get_latest_bars_values(self, symbol: str, val_type: str = "close", N: int = 1) -> np.ndarray:
        bars = self.get_latest_bars(symbol, N=N)
        if not bars:
            return np.array([])
        v_type = val_type.lower()
        return np.array([b.get(v_type, 0.0) for b in bars], dtype=np.float64)

    def total_bars(self) -> int:
        return len(self.all_dates)

    def current_bar_idx(self) -> int:
        return self.bar_index

