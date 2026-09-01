"""Sector Data Ingestion Pipeline for Yahoo Finance, CSV, and Synthetic Data."""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from sector_quant.db.master import SecuritiesMaster
from sector_quant.db.adjustments import calculate_corporate_action_adjustments

logger = logging.getLogger(__name__)


class SectorDataIngestor:
    """Pipelines historical price feeds into Securities Master with validation & adjustments."""

    def __init__(self, master: SecuritiesMaster):
        self.master = master

    def ingest_from_dataframe(
        self,
        ticker: str,
        df: pd.DataFrame,
        exchange_code: str = "NYSE",
        sector_code: Optional[str] = None,
        security_name: str = "",
        splits: Optional[List[Dict[str, Any]]] = None,
        dividends: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Processes raw price DataFrame, computes corporate action adjustments, and stores in master."""
        self.master.register_symbol(
            ticker=ticker,
            exchange_code=exchange_code,
            sector_code=sector_code,
            security_name=security_name or ticker,
        )

        adjusted_df = calculate_corporate_action_adjustments(df, splits=splits, dividends=dividends)
        inserted_count = self.master.insert_daily_prices(ticker, adjusted_df)

        if splits:
            for s in splits:
                self.master.insert_corporate_action(
                    ticker=ticker,
                    ex_date=str(s.get("date"))[:10],
                    action_type="SPLIT",
                    value=float(s.get("ratio", 1.0)),
                    split_ratio=float(s.get("ratio", 1.0)),
                )

        if dividends:
            for d in dividends:
                self.master.insert_corporate_action(
                    ticker=ticker,
                    ex_date=str(d.get("date"))[:10],
                    action_type="DIVIDEND",
                    value=float(d.get("dividend", 0.0)),
                    cash_amount=float(d.get("dividend", 0.0)),
                )

        return inserted_count

    def ingest_from_csv(
        self,
        ticker: str,
        csv_filepath: str,
        exchange_code: str = "NYSE",
        sector_code: Optional[str] = None,
        security_name: str = "",
    ) -> int:
        """Ingests price data from a CSV file."""
        if not os.path.exists(csv_filepath):
            raise FileNotFoundError(f"CSV file not found: {csv_filepath}")

        df = pd.read_csv(csv_filepath)
        # Normalize column names to lowercase
        df.columns = [c.strip().lower() for c in df.columns]
        return self.ingest_from_dataframe(
            ticker=ticker,
            df=df,
            exchange_code=exchange_code,
            sector_code=sector_code,
            security_name=security_name,
        )

    def ingest_from_yfinance(
        self,
        tickers: List[str],
        sector_code: str,
        start_date: str,
        end_date: str,
        exchange_code: str = "NYSE",
    ) -> Dict[str, int]:
        """Downloads historical bars via yfinance if library is available."""
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance is not installed; skipping yfinance ingestion.")
            return {}

        results = {}
        for ticker in tickers:
            try:
                data = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False)
                if data.empty:
                    logger.warning(f"No data returned for {ticker}")
                    continue

                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = [col[0].lower() for col in data.columns]
                else:
                    data.columns = [col.lower() for col in data.columns]

                data["date"] = data.index
                count = self.ingest_from_dataframe(
                    ticker=ticker,
                    df=data,
                    exchange_code=exchange_code,
                    sector_code=sector_code,
                )
                results[ticker] = count
            except Exception as e:
                logger.error(f"Failed to ingest {ticker} from yfinance: {e}")

        return results

    def generate_synthetic_sector_data(
        self,
        sector_code: str,
        benchmark_ticker: str,
        constituent_tickers: List[str],
        start_date: str = "2023-01-01",
        num_days: int = 252,
        seed: int = 42,
        cointegrated_pairs: Optional[List[tuple[str, str, float]]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Generates realistic synthetic sector data with benchmark correlation and cointegrated pairs.

        Parameters
        ----------
        sector_code : str
            Sector identifier (e.g. 'ENERGY', 'TECH').
        benchmark_ticker : str
            Sector benchmark ETF (e.g. 'XLE', 'XLK').
        constituent_tickers : list of str
            Constituents tickers in the sector.
        start_date : str
            Start date string (YYYY-MM-DD).
        num_days : int
            Number of trading days to simulate.
        seed : int
            Random seed for determinism.
        cointegrated_pairs : list of (symbol_y, symbol_x, hedge_ratio)
            Pairs to enforce cointegration with mean-reverting spread.

        Returns
        -------
        dict of str -> pd.DataFrame
        """
        np.random.seed(seed)
        dates = pd.bdate_range(start=start_date, periods=num_days)

        # 1. Generate benchmark ETF returns (GBM with sector drift)
        daily_drift = 0.0004
        daily_vol = 0.012
        bench_ret = np.random.normal(daily_drift, daily_vol, num_days)
        bench_price = 100.0 * np.exp(np.cumsum(bench_ret))

        bench_df = pd.DataFrame({
            "date": dates,
            "open": bench_price * (1 + np.random.uniform(-0.003, 0.003, num_days)),
            "high": bench_price * (1 + np.random.uniform(0.002, 0.01, num_days)),
            "low": bench_price * (1 - np.random.uniform(0.002, 0.01, num_days)),
            "close": bench_price,
            "volume": np.random.randint(1_000_000, 5_000_000, num_days),
        })
        bench_df["adj_close"] = bench_df["close"]
        bench_df["adj_factor"] = 1.0

        generated_dfs = {benchmark_ticker: bench_df}
        self.ingest_from_dataframe(benchmark_ticker, bench_df, sector_code=sector_code)

        # 2. Generate constituents
        pair_dict = {}
        if cointegrated_pairs:
            for y_sym, x_sym, hedge_ratio in cointegrated_pairs:
                pair_dict[y_sym] = (x_sym, hedge_ratio)

        for sym in constituent_tickers:
            if sym in pair_dict:
                continue  # Will generate pair dependent on X

            # Individual stock beta to benchmark + idiosyncratic shock
            beta = np.random.uniform(0.7, 1.4)
            idio_vol = np.random.uniform(0.008, 0.018)
            stock_ret = beta * bench_ret + np.random.normal(0, idio_vol, num_days)
            base_price = np.random.uniform(50.0, 200.0)
            stock_price = base_price * np.exp(np.cumsum(stock_ret))

            df = pd.DataFrame({
                "date": dates,
                "open": stock_price * (1 + np.random.uniform(-0.004, 0.004, num_days)),
                "high": stock_price * (1 + np.random.uniform(0.003, 0.015, num_days)),
                "low": stock_price * (1 - np.random.uniform(0.003, 0.015, num_days)),
                "close": stock_price,
                "volume": np.random.randint(500_000, 2_500_000, num_days),
            })
            df["adj_close"] = df["close"]
            df["adj_factor"] = 1.0

            generated_dfs[sym] = df
            self.ingest_from_dataframe(sym, df, sector_code=sector_code)

        # 3. Generate cointegrated pairs: Y = beta * X + stationary_OU_process
        if cointegrated_pairs:
            for y_sym, x_sym, hedge_ratio in cointegrated_pairs:
                x_df = generated_dfs.get(x_sym)
                if x_df is None:
                    continue
                x_close = x_df["close"].values

                # Mean-reverting Ornstein-Uhlenbeck process for spread
                # dS = theta * (mu - S) dt + sigma dW
                theta = 0.15  # Speed of mean reversion
                spread_mu = 5.0
                spread_sigma = 1.2
                spread = np.zeros(num_days)
                spread[0] = spread_mu
                for t in range(1, num_days):
                    spread[t] = spread[t - 1] + theta * (spread_mu - spread[t - 1]) + np.random.normal(0, spread_sigma)

                y_close = hedge_ratio * x_close + spread
                # Ensure positive prices
                y_close = np.maximum(y_close, 5.0)

                y_df = pd.DataFrame({
                    "date": dates,
                    "open": y_close * (1 + np.random.uniform(-0.004, 0.004, num_days)),
                    "high": y_close * (1 + np.random.uniform(0.003, 0.015, num_days)),
                    "low": y_close * (1 - np.random.uniform(0.003, 0.015, num_days)),
                    "close": y_close,
                    "volume": np.random.randint(500_000, 2_500_000, num_days),
                })
                y_df["adj_close"] = y_df["close"]
                y_df["adj_factor"] = 1.0

                generated_dfs[y_sym] = y_df
                self.ingest_from_dataframe(y_sym, y_df, sector_code=sector_code)

        return generated_dfs

