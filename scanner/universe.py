"""NIFTY Universes & Liquidity Screening for Indian Equities (Project-Beta)."""

from __future__ import annotations

import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Standard Indian Stock Universes (NSE Symbols)
NIFTY_50_STOCKS: Dict[str, Dict[str, str]] = {
    "RELIANCE": {"sector": "ENERGY", "token": "738561", "lot_size": "250"},
    "TCS": {"sector": "IT", "token": "2953217", "lot_size": "175"},
    "INFY": {"sector": "IT", "token": "408065", "lot_size": "400"},
    "HDFCBANK": {"sector": "BANKING", "token": "341249", "lot_size": "550"},
    "ICICIBANK": {"sector": "BANKING", "token": "1270529", "lot_size": "700"},
    "SBIN": {"sector": "BANKING", "token": "779521", "lot_size": "750"},
    "BHARTIARTL": {"sector": "TELECOM", "token": "2714625", "lot_size": "475"},
    "ITC": {"sector": "FMCG", "token": "424961", "lot_size": "1600"},
    "KOTAKBANK": {"sector": "BANKING", "token": "492033", "lot_size": "400"},
    "LT": {"sector": "INFRA", "token": "2939649", "lot_size": "150"},
    "AXISBANK": {"sector": "BANKING", "token": "1510401", "lot_size": "625"},
    "ASIANPAINT": {"sector": "CONSUMER", "token": "60417", "lot_size": "200"},
    "MARUTI": {"sector": "AUTO", "token": "2815745", "lot_size": "50"},
    "TITAN": {"sector": "CONSUMER", "token": "897281", "lot_size": "175"},
    "SUNPHARMA": {"sector": "PHARMA", "token": "857857", "lot_size": "350"},
    "BAJFINANCE": {"sector": "FINANCIAL", "token": "81153", "lot_size": "125"},
    "TATAMOTORS": {"sector": "AUTO", "token": "884737", "lot_size": "575"},
    "TATASTEEL": {"sector": "METALS", "token": "895745", "lot_size": "5500"},
    "NTPC": {"sector": "POWER", "token": "2977281", "lot_size": "1500"},
    "POWERGRID": {"sector": "POWER", "token": "3834113", "lot_size": "1800"},
}

NIFTY_100_ADDITIONAL: Dict[str, Dict[str, str]] = {
    "M&M": {"sector": "AUTO", "token": "519937", "lot_size": "350"},
    "HCLTECH": {"sector": "IT", "token": "1850625", "lot_size": "350"},
    "WIPRO": {"sector": "IT", "token": "969473", "lot_size": "1500"},
    "COALINDIA": {"sector": "METALS", "token": "5215745", "lot_size": "2100"},
    "ONGC": {"sector": "ENERGY", "token": "633601", "lot_size": "3850"},
    "BAJAJFINSV": {"sector": "FINANCIAL", "token": "4267265", "lot_size": "250"},
    "NESTLEIND": {"sector": "FMCG", "token": "4598529", "lot_size": "25"},
    "TECHM": {"sector": "IT", "token": "3465729", "lot_size": "600"},
    "JSWSTEEL": {"sector": "METALS", "token": "3001089", "lot_size": "675"},
    "HINDALCO": {"sector": "METALS", "token": "348929", "lot_size": "1400"},
}


def get_universe_symbols(universe_name: str = "NIFTY_50") -> List[Dict[str, str]]:
    """Returns stock symbols with their respective sectors and lot sizes."""
    if universe_name.upper() == "NIFTY_50":
        return [{"symbol": k, "sector": v["sector"], "lot_size": v["lot_size"]} for k, v in NIFTY_50_STOCKS.items()]
    elif universe_name.upper() in ("NIFTY_100", "NIFTY_500"):
        combined = {**NIFTY_50_STOCKS, **NIFTY_100_ADDITIONAL}
        return [{"symbol": k, "sector": v["sector"], "lot_size": v["lot_size"]} for k, v in combined.items()]
    return [{"symbol": k, "sector": v["sector"], "lot_size": v["lot_size"]} for k, v in NIFTY_50_STOCKS.items()]
