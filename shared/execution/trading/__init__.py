"""
PROJECT-ALPHA Trading Subsystem Package.
"""

from __future__ import annotations

from .precision_rules import (
    PRECISION_TABLE,
    PairPrecisionSpec,
    get_pair_spec,
    round_price,
    round_qty,
    validate_order_notional,
)
from .subaccount_manager import (
    CoinDCXExecutionClient,
    CoinDCXExecutionManager,
    CoinDCXSubAccountClient,
    CoinDCXSubAccountManager,
    SubAccountConfig,
)
from .broker_manager import IndianBrokerExecutionClient, IndianBrokerManager

__all__ = [
    "PRECISION_TABLE",
    "CoinDCXExecutionClient",
    "CoinDCXExecutionManager",
    "CoinDCXSubAccountClient",
    "CoinDCXSubAccountManager",
    "PairPrecisionSpec",
    "IndianBrokerExecutionClient",
    "IndianBrokerManager",
    "SubAccountConfig",
    "get_pair_spec",
    "round_price",
    "round_qty",
    "validate_order_notional",
]
