"""
V2 Public Market Data Package.
"""

from .feeder import MarketFeeder
from .public_client import IndianStockPublicClient, TokenBucketRateLimiter
from .session import BSE_SESSION, NSE_SESSION, MarketSession, is_stock_market_open
from .symbols import StockSymbol, validate_share_quantity
from .models import MarketQuote
from .provider import MarketDataProvider, YahooMarketDataProvider

CoinDCXPublicClient = IndianStockPublicClient

__all__ = [
    "CoinDCXPublicClient",
    "IndianStockPublicClient",
    "MarketFeeder",
    "MarketSession",
    "NSE_SESSION",
    "BSE_SESSION",
    "TokenBucketRateLimiter",
    "StockSymbol",
    "validate_share_quantity",
    "MarketQuote",
    "MarketDataProvider",
    "YahooMarketDataProvider",
    "is_stock_market_open",
]
