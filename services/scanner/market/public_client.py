"""Scanner-local exports for the shared Indian public market client."""

from shared.market.public_client import (
    DEFAULT_NSE_SYMBOLS,
    IndianStockPublicClient,
    TokenBucketRateLimiter,
)

CoinDCXPublicClient = IndianStockPublicClient

__all__ = [
    "DEFAULT_NSE_SYMBOLS",
    "IndianStockPublicClient",
    "CoinDCXPublicClient",
    "TokenBucketRateLimiter",
]
