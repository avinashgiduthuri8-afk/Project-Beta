"""Stages 6–7: risk/allocation and durable paper execution."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from core.repository.paper_execution_repo import PaperExecutionRepository
from execution.indian_charges import IndianEquityChargeModel
from market.models import MarketQuote
from market.symbols import StockSymbol
from paper.execution.fill_simulator import PaperFillSimulator, PaperOrder


class IndianPaperService:
    def __init__(self, repo: PaperExecutionRepository, allocation: Decimal = Decimal("10000")) -> None:
        self.repo = repo
        self.allocation = Decimal(allocation)
        self.charges = IndianEquityChargeModel()
        self.fills = PaperFillSimulator()

    async def handle_signal(self, signal: dict, quote: MarketQuote, now: datetime) -> dict:
        if str(signal.get("mode", "")).upper() != "PAPER":
            return {"stages": [6], "opened": False, "reason": "PAPER_ONLY"}
        symbol = StockSymbol(signal["exchange"], signal["symbol"], signal.get("security_id"))
        if symbol.canonical != quote.symbol.canonical:
            return {"stages": [6], "opened": False, "reason": "QUOTE_SYMBOL_MISMATCH"}
        balance = await self.repo.cash()
        budget = min(balance, self.allocation)
        quantity = int(budget // quote.price)
        while quantity > 0:
            charge = self.charges.estimate("BUY", quote.price, quantity, symbol.exchange)
            debit = quote.price * quantity + charge.total
            if debit <= budget:
                break
            quantity -= 1
        if quantity <= 0:
            return {"stages": [6], "opened": False, "reason": "INSUFFICIENT_CASH"}
        order = PaperOrder(f"paper-{signal['id']}", "BUY", "MARKET", quantity)
        fill = self.fills.fill(order, quote, now)
        if not fill:
            return {"stages": [6], "opened": False, "reason": "STALE_OR_UNFILLED_QUOTE"}
        position_id = "pos-" + sha256(str(signal["id"]).encode()).hexdigest()[:16]
        opened = await self.repo.open_position_once(
            str(signal["id"]),
            {"id": position_id, "symbol": symbol.trading_symbol, "exchange": symbol.exchange,
             "quantity": quantity, "entry_price": fill.price, "charges": charge.total,
             "opened_at": now.isoformat()},
            debit,
        )
        return {"stages": [6, 7], "opened": opened, "reason": None if opened else "SIGNAL_ALREADY_CONSUMED", "position_id": position_id, "quantity": quantity, "charges": charge.as_dict()}
