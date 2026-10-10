"""NSE/BSE cash-equity tick, lot, and order validation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class PairPrecisionSpec:
    pair: str
    base_price: float = 0.0
    price_decimals: int = 2
    lot_step_decimals: int = 0
    min_lot_qty: float = 1.0
    min_notional_inr: float = 0.0
    min_notional_usdt: float = 0.0
    quote_currency: str = "INR"
    tick_size: float = 0.05

    @property
    def min_quantity(self) -> float:
        return self.min_lot_qty

    def __getitem__(self, item: str) -> Any:
        if item == "min_quantity":
            return self.min_lot_qty
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)


DEFAULT_SPEC = PairPrecisionSpec(pair="NSE:CASH")
PRECISION_TABLE: dict[str, PairPrecisionSpec] = {
    symbol: PairPrecisionSpec(pair=f"NSE:{symbol}")
    for symbol in (
        "RELIANCE",
        "HDFCBANK",
        "TCS",
        "INFY",
        "ICICIBANK",
        "SBIN",
        "ITC",
        "LT",
        "BHARTIARTL",
        "HINDUNILVR",
    )
}


def normalize_price(raw_price: Any) -> float:
    if raw_price is None:
        raise ValueError("Price cannot be None")
    try:
        value = float(str(raw_price).strip().replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot parse price from {raw_price!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid price value: {raw_price}")
    return value


def normalize_qty(raw_qty: Any) -> float:
    if raw_qty is None:
        raise ValueError("Quantity cannot be None")
    try:
        value = float(str(raw_qty).strip().replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot parse quantity from {raw_qty!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid quantity value: {raw_qty}")
    return value


def extract_base_coin(symbol: str | None) -> str:
    """Compatibility helper returning an Indian equity trading symbol."""
    if not symbol:
        return ""
    clean = str(symbol).upper().strip().replace(".NS", "").replace(".BO", "")
    if ":" in clean:
        clean = clean.split(":", 1)[1]
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    if clean.endswith("INR") and len(clean) > 3:
        clean = clean[:-3]
    return clean.strip()


def get_pair_spec(pair: str, reference_price: float | None = None) -> PairPrecisionSpec:
    symbol = extract_base_coin(pair)
    return PRECISION_TABLE.get(symbol, PairPrecisionSpec(pair=f"NSE:{symbol or 'CASH'}"))


def infer_price_decimals(price: float) -> int:
    return 2


def infer_lot_decimals(price: float) -> int:
    return 0


def round_price(pair: str, price: float) -> float:
    if not math.isfinite(price) or price <= 0:
        return 0.0
    tick = Decimal(str(get_pair_spec(pair, price).tick_size))
    value = Decimal(str(price))
    return float((value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick)


def round_qty(pair: str, qty: float) -> float:
    if not math.isfinite(qty) or qty <= 0:
        return 0.0
    return float(Decimal(str(qty)).quantize(Decimal("1"), rounding=ROUND_FLOOR))


round_qty_down = round_qty


def round_qty_up(pair: str, qty: float) -> float:
    if not math.isfinite(qty) or qty <= 0:
        return 0.0
    return float(Decimal(str(qty)).quantize(Decimal("1"), rounding=ROUND_CEILING))


def validate_order_notional(
    pair: str,
    price: float,
    qty: float,
    min_notional: float | None = None,
    usdt_inr_rate: float = 1.0,
) -> bool:
    if not all(math.isfinite(v) and v > 0 for v in (price, qty)):
        return False
    if int(qty) != qty:
        return False
    minimum = get_pair_spec(pair, price).min_notional_inr if min_notional is None else min_notional
    return qty >= 1 and price * qty >= max(0.0, float(minimum)) * 0.99


def validate_trade_parameters(
    pair: str,
    price: float,
    qty: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    is_long: bool = True,
    min_notional: float = 0.0,
    usdt_inr_rate: float = 1.0,
) -> tuple[bool, str | None]:
    if not validate_order_notional(pair, price, qty, min_notional, usdt_inr_rate):
        return False, "Price, whole-share quantity, or order value is invalid"
    if stop_loss is not None:
        if stop_loss <= 0 or (is_long and stop_loss >= price) or (not is_long and stop_loss <= price):
            return False, "Stop loss is on the wrong side of entry"
    if take_profit is not None:
        if take_profit <= 0 or (is_long and take_profit <= price) or (not is_long and take_profit >= price):
            return False, "Take profit is on the wrong side of entry"
    return True, None
