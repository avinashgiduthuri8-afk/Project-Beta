"""
Sample Intraday VWAP Momentum Strategy for Indian Equities.
Calculates cumulative VWAP and triggers Buy/Sell signals on breakout with stop loss.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional
from core.enums import Exchange, OrderSide, OrderType, ProductType
from core.models import Candle, Order, OrderRequest, Tick
from strategies.base_strategy import Strategy

logger = logging.getLogger(__name__)


class VWAPMomentumStrategy(Strategy):
    """
    Intraday strategy that tracks Volume Weighted Average Price (VWAP)
    and executes momentum trades when price crosses above/below VWAP.
    """

    def __init__(
        self,
        symbols: List[str],
        submit_order_fn: Optional[Callable[[OrderRequest], Order]] = None,
        quantity_per_trade: int = 10,
    ) -> None:
        super().__init__(name="VWAP_Momentum", submit_order_fn=submit_order_fn)
        self.symbols = symbols
        self.quantity_per_trade = quantity_per_trade

        # Tracking state
        self._cumulative_pv: Dict[str, float] = {s: 0.0 for s in symbols}
        self._cumulative_vol: Dict[str, int] = {s: 0 for s in symbols}
        self._vwap: Dict[str, float] = {s: 0.0 for s in symbols}
        self._in_position: Dict[str, bool] = {s: False for s in symbols}

    def on_tick(self, tick: Tick) -> None:
        if tick.symbol not in self.symbols:
            return

        vol = max(1, tick.last_quantity or 1)
        self._cumulative_pv[tick.symbol] += tick.last_price * vol
        self._cumulative_vol[tick.symbol] += vol
        self._vwap[tick.symbol] = self._cumulative_pv[tick.symbol] / self._cumulative_vol[tick.symbol]

    def on_candle(self, candle: Candle) -> None:
        symbol = candle.symbol
        if symbol not in self.symbols or not candle.is_closed:
            return

        current_vwap = self._vwap.get(symbol, 0.0)
        if current_vwap <= 0.0:
            return

        logger.info(
            f"Strategy [{self.name}] {symbol} candle closed: Close=₹{candle.close:.2f}, VWAP=₹{current_vwap:.2f}"
        )

        # Bullish signal: Candle closes above VWAP and not currently in position
        if candle.close > current_vwap * 1.002 and not self._in_position[symbol]:
            logger.info(f"🟢 Signal generated: BUY {symbol} @ ₹{candle.close:.2f} (Above VWAP)")
            order = self.buy(
                symbol=symbol,
                quantity=self.quantity_per_trade,
                price=candle.close,
                product=ProductType.MIS,
                order_type=OrderType.LIMIT,
            )
            if order and order.status.value in ("OPEN", "COMPLETE"):
                self._in_position[symbol] = True

        # Bearish exit / Short signal
        elif candle.close < current_vwap * 0.998 and self._in_position[symbol]:
            logger.info(f"🔴 Signal generated: SELL/EXIT {symbol} @ ₹{candle.close:.2f} (Below VWAP)")
            order = self.sell(
                symbol=symbol,
                quantity=self.quantity_per_trade,
                price=candle.close,
                product=ProductType.MIS,
                order_type=OrderType.LIMIT,
            )
            if order and order.status.value in ("OPEN", "COMPLETE"):
                self._in_position[symbol] = False
