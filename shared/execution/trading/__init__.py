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
__all__ = [
    "PRECISION_TABLE",
    "PairPrecisionSpec",
    "get_pair_spec",
    "round_price",
    "round_qty",
    "validate_order_notional",
]
