"""Transparent estimated charges for Indian cash-equity paper fills."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP

from market.symbols import validate_share_quantity

PAISE = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class ChargeBreakdown:
    brokerage: Decimal
    stt: Decimal
    exchange_charge: Decimal
    sebi_charge: Decimal
    gst: Decimal
    stamp_duty: Decimal
    slippage: Decimal
    total: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return asdict(self)


class IndianEquityChargeModel:
    """Conservative configurable estimate; this is not a tax statement."""

    def estimate(self, side: str, price: Decimal, quantity: int, exchange: str, intraday: bool = False) -> ChargeBreakdown:
        side = side.upper()
        exchange = exchange.upper()
        qty = validate_share_quantity(quantity)
        price = Decimal(price)
        if side not in {"BUY", "SELL"} or exchange not in {"NSE", "BSE"} or price <= 0:
            raise ValueError("INVALID_CHARGE_INPUT")
        turnover = price * qty
        brokerage = _money(min(Decimal("20"), turnover * (Decimal("0.0003") if intraday else Decimal("0"))))
        stt = _money(turnover * (Decimal("0.00025") if intraday and side == "SELL" else Decimal("0.001")))
        exchange_charge = _money(turnover * (Decimal("0.0000297") if exchange == "NSE" else Decimal("0.0000375")))
        sebi_charge = _money(turnover * Decimal("0.000001"))
        gst = _money((brokerage + exchange_charge + sebi_charge) * Decimal("0.18"))
        stamp_duty = _money(turnover * Decimal("0.00015")) if side == "BUY" else Decimal("0.00")
        slippage = _money(turnover * Decimal("0.0005"))
        total = _money(brokerage + stt + exchange_charge + sebi_charge + gst + stamp_duty + slippage)
        return ChargeBreakdown(brokerage, stt, exchange_charge, sebi_charge, gst, stamp_duty, slippage, total)
