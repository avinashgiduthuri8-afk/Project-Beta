"""Corporate Action Adjustment formulas for backward price & volume adjustments."""

from __future__ import annotations

from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np


def calculate_corporate_action_adjustments(
    price_df: pd.DataFrame,
    splits: Optional[List[Dict[str, Any]]] = None,
    dividends: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Calculates cumulative adjustment factors and adjusted OHLCV bars.

    Parameters
    ----------
    price_df : pd.DataFrame
        DataFrame with columns ['date' (or DatetimeIndex), 'open', 'high', 'low', 'close', 'volume'].
    splits : list of dict, optional
        List of dicts with keys: 'date' (YYYY-MM-DD or date object), 'ratio' (e.g. 2.0 for 2-for-1 split).
    dividends : list of dict, optional
        List of dicts with keys: 'date' (YYYY-MM-DD or date object), 'dividend' (cash amount per share).

    Returns
    -------
    pd.DataFrame
        DataFrame with added/updated columns ['adj_factor', 'adj_open', 'adj_high', 'adj_low', 'adj_close', 'adj_volume'].
    """
    df = price_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
        elif "price_date" in df.columns:
            df["date"] = pd.to_datetime(df["price_date"])
            df = df.sort_values("date").reset_index(drop=True)

    n_rows = len(df)
    if n_rows == 0:
        df["adj_factor"] = []
        df["adj_open"] = []
        df["adj_high"] = []
        df["adj_low"] = []
        df["adj_close"] = []
        df["adj_volume"] = []
        return df

    # Initialize daily adjustment multiplier to 1.0
    # Multiplier on day t modifies all days prior to t (< t)
    split_mult = np.ones(n_rows, dtype=np.float64)
    div_mult = np.ones(n_rows, dtype=np.float64)

    dates = pd.to_datetime(df["date"] if "date" in df.columns else df.index).dt.strftime("%Y-%m-%d").values
    close_prices = df["close"].values if "close" in df.columns else df["close_price"].values

    date_to_idx = {d: i for i, d in enumerate(dates)}

    # Apply splits: a 2-for-1 split on date T divides prior prices by 2.0
    if splits:
        for sp in splits:
            sp_date = str(sp.get("date"))[:10]
            ratio = float(sp.get("ratio", 1.0))
            if ratio > 0 and sp_date in date_to_idx:
                idx = date_to_idx[sp_date]
                if idx > 0:
                    split_mult[idx] *= (1.0 / ratio)

    # Apply dividends: dividend D on date T reduces prior prices by (1 - D / P_{T-1})
    if dividends:
        for div in dividends:
            div_date = str(div.get("date"))[:10]
            cash_div = float(div.get("dividend", div.get("value", 0.0)))
            if cash_div > 0 and div_date in date_to_idx:
                idx = date_to_idx[div_date]
                if idx > 0:
                    prev_close = close_prices[idx - 1]
                    if prev_close > cash_div:
                        div_factor = (prev_close - cash_div) / prev_close
                        div_mult[idx] *= div_factor

    # Combined daily multiplier on day t
    daily_mult = split_mult * div_mult

    # Cumulative backward adjustment factor:
    # Factor at day t is the product of all daily multipliers from t+1 to end of series
    # At the last day (most recent), factor = 1.0
    cum_factor = np.ones(n_rows, dtype=np.float64)
    running_factor = 1.0
    for i in range(n_rows - 1, -1, -1):
        running_factor *= daily_mult[i]
        cum_factor[i] = running_factor

    # Normalize so the last bar has factor 1.0 if running_factor at end is baseline
    if cum_factor[-1] != 0:
        cum_factor = cum_factor / cum_factor[-1]

    open_col = "open" if "open" in df.columns else "open_price"
    high_col = "high" if "high" in df.columns else "high_price"
    low_col = "low" if "low" in df.columns else "low_price"
    close_col = "close" if "close" in df.columns else "close_price"
    vol_col = "volume" if "volume" in df.columns else "vol"

    df["adj_factor"] = cum_factor
    df["adj_open"] = df[open_col] * cum_factor
    df["adj_high"] = df[high_col] * cum_factor
    df["adj_low"] = df[low_col] * cum_factor
    df["adj_close"] = df[close_col] * cum_factor
    # For volume, backward split-adjusted volume increases when prices decrease
    df["adj_volume"] = (df[vol_col] / cum_factor).round().astype(np.int64)

    return df


def adjust_ohlcv_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Helper to ensure adjusted columns exist; falls back to raw if already adjusted."""
    res = df.copy()
    if "adj_close" not in res.columns and "adj_close_price" not in res.columns:
        if "close" in res.columns:
            res["adj_close"] = res["close"]
        elif "close_price" in res.columns:
            res["adj_close"] = res["close_price"]

    for col in ["open", "high", "low", "close"]:
        if f"adj_{col}" not in res.columns and col in res.columns:
            res[f"adj_{col}"] = res[col]

    if "adj_volume" not in res.columns and "volume" in res.columns:
        res["adj_volume"] = res["volume"]

    if "adj_factor" not in res.columns:
        res["adj_factor"] = 1.0

    return res

