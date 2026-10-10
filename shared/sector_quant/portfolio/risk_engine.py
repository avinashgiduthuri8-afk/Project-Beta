"""
sector_quant.portfolio.risk_engine — Sector exposure caps & single-stock risk gates.
"""

from __future__ import annotations

from sector_quant.events import FillEvent, OrderEvent, SignalEvent


class SectorRiskEngine:
    """
    Portfolio risk controller enforcing:
    1. Maximum single-stock exposure cap (default 15% of portfolio equity).
    2. Maximum sector exposure cap (default 30% of portfolio equity).
    3. Proper position sizing and order generation from SignalEvent.
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        max_sector_exposure_pct: float = 0.30,
        max_stock_exposure_pct: float = 0.15,
        symbol_sector_map: dict[str, str] | None = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.current_cash = initial_capital
        self.max_sector_pct = max_sector_exposure_pct
        self.max_stock_pct = max_stock_exposure_pct
        self.symbol_sector_map: dict[str, str] = {
            k.upper(): v.upper() for k, v in (symbol_sector_map or {}).items()
        }

        # Positions tracking: symbol -> signed quantity (+ for long, - for short)
        self.positions: dict[str, int] = {}
        # Current market prices: symbol -> price
        self.current_prices: dict[str, float] = {}

    def update_price(self, symbol: str, price: float) -> None:
        self.current_prices[symbol.upper()] = price

    def get_portfolio_equity(self) -> float:
        """Total equity = Cash + Unrealized value of positions."""
        holdings_value = 0.0
        for sym, qty in self.positions.items():
            price = self.current_prices.get(sym, 0.0)
            holdings_value += qty * price
        return self.current_cash + holdings_value

    def get_stock_exposure(self, symbol: str) -> float:
        """Current notional exposure of a single stock."""
        sym = symbol.upper()
        qty = abs(self.positions.get(sym, 0))
        price = self.current_prices.get(sym, 0.0)
        return qty * price

    def get_sector_exposure(self, sector: str) -> float:
        """Total notional exposure of all stocks in a sector."""
        sec = sector.upper()
        total = 0.0
        for sym, qty in self.positions.items():
            if self.symbol_sector_map.get(sym) == sec:
                price = self.current_prices.get(sym, 0.0)
                total += abs(qty) * price
        return total

    def update_fill(self, fill: FillEvent) -> None:
        """Update cash and positions when FillEvent arrives."""
        sym = fill.symbol.upper()
        fill_cost = fill.fill_cost
        commission = fill.commission
        direction = fill.direction.upper()
        qty = fill.quantity

        if direction == "BUY":
            self.current_cash -= fill_cost + commission
            self.positions[sym] = self.positions.get(sym, 0) + qty
        elif direction == "SELL":
            self.current_cash += fill_cost - commission
            self.positions[sym] = self.positions.get(sym, 0) - qty

        # Clean up zero positions
        if self.positions.get(sym, 0) == 0:
            self.positions.pop(sym, None)

    def evaluate_order(
        self,
        signal: SignalEvent,
        current_price: float,
        target_allocation_pct: float = 0.10,
    ) -> OrderEvent | None:
        """
        Evaluate SignalEvent, enforce sector & stock risk caps,
        and generate a compliant OrderEvent.
        """
        sym = signal.symbol.upper()
        self.current_prices[sym] = current_price
        equity = max(1.0, self.get_portfolio_equity())
        sector = self.symbol_sector_map.get(sym, "UNKNOWN")

        # 1. Exit Logic
        if signal.signal_type == "EXIT":
            current_pos = self.positions.get(sym, 0)
            if current_pos > 0:
                return OrderEvent(sym, "MKT", abs(current_pos), "SELL", current_price)
            elif current_pos < 0:
                return OrderEvent(sym, "MKT", abs(current_pos), "BUY", current_price)
            return None

        # 2. Entry Sizing Logic
        desired_notional = equity * target_allocation_pct * signal.strength
        max_stock_capital = equity * self.max_stock_pct
        max_sector_capital = equity * self.max_sector_pct

        curr_stock_exp = self.get_stock_exposure(sym)
        curr_sector_exp = self.get_sector_exposure(sector)

        # Enforce Stock Cap
        available_stock_headroom = max(0.0, max_stock_capital - curr_stock_exp)
        # Enforce Sector Cap
        available_sector_headroom = max(0.0, max_sector_capital - curr_sector_exp)

        allowed_notional = min(
            desired_notional, available_stock_headroom, available_sector_headroom
        )
        if allowed_notional < current_price:
            # Cannot afford even 1 share within risk bounds
            return None

        allowed_qty = int(allowed_notional // current_price)
        if allowed_qty <= 0:
            return None

        direction = "BUY" if signal.signal_type == "LONG" else "SELL"
        return OrderEvent(sym, "MKT", allowed_qty, direction, current_price)
