"""
sector_quant.strategies.pairs_trading — Statistical arbitrage & rolling OLS cointegration strategy.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import datetime
from queue import Queue

from sector_quant.data.base import DataHandler
from sector_quant.events import Event, SignalEvent


class Strategy(ABC):
    """Abstract Strategy base class."""

    def __init__(self, bars: DataHandler, events_queue: Queue) -> None:
        self.bars = bars
        self.events = events_queue

    @abstractmethod
    def calculate_signals(self, event: Event) -> None:
        raise NotImplementedError


class PairsTradingStrategy(Strategy):
    """
    Statistical Arbitrage Pairs Trading Strategy.
    Calculates rolling OLS regression of Y against X, computes spread z-score,
    and emits paired SignalEvent instances on mean-reversion triggers.
    """

    def __init__(
        self,
        bars: DataHandler,
        events_queue: Queue,
        pair: tuple[str, str],
        lookback_window: int = 20,
        z_entry: float = 2.0,
        z_exit: float = 0.5,
        strategy_id: str = "PAIRS_OLS_V1",
    ) -> None:
        super().__init__(bars, events_queue)
        self.sym_y, self.sym_x = pair[0].upper(), pair[1].upper()
        self.lookback = lookback_window
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.strategy_id = strategy_id

        # Internal state
        self.in_market: str | None = None  # None, "LONG_SPREAD", "SHORT_SPREAD"
        self.hedge_ratio: float = 1.0
        self.last_z_score: float = 0.0

    def _compute_rolling_ols(
        self, y: list[float], x: list[float]
    ) -> tuple[float, float, float]:
        """
        Compute Ordinary Least Squares regression Y = beta * X + alpha.
        Returns (beta, alpha, residual_std).
        """
        n = len(y)
        if n != len(x) or n < 2:
            return 1.0, 0.0, 1.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        var_x = sum((xi - mean_x) ** 2 for xi in x)
        if var_x == 0:
            return 1.0, 0.0, 1.0

        cov_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        beta = cov_xy / var_x
        alpha = mean_y - beta * mean_x

        residuals = [yi - (beta * xi + alpha) for xi, yi in zip(x, y)]
        mean_res = sum(residuals) / n
        var_res = (
            sum((r - mean_res) ** 2 for r in residuals) / (n - 1) if n > 1 else 1.0
        )
        std_res = math.sqrt(max(1e-8, var_res))

        return beta, alpha, std_res

    def calculate_signals(self, event: Event) -> None:
        """Process MarketEvent to compute spread z-score and emit SignalEvent."""
        if event.type != "MARKET":
            return

        y_prices = self.bars.get_latest_bars_values(
            self.sym_y, "close", N=self.lookback
        )
        x_prices = self.bars.get_latest_bars_values(
            self.sym_x, "close", N=self.lookback
        )

        if len(y_prices) < self.lookback or len(x_prices) < self.lookback:
            return

        beta, alpha, std_res = self._compute_rolling_ols(y_prices, x_prices)
        self.hedge_ratio = beta

        # Current spread
        curr_y = y_prices[-1]
        curr_x = x_prices[-1]
        curr_spread = curr_y - (beta * curr_x + alpha)
        z_score = curr_spread / std_res if std_res > 0 else 0.0
        self.last_z_score = z_score

        dt = self.bars.get_latest_bar_datetime(self.sym_y) or datetime.utcnow()

        # Signal Logic
        if self.in_market is None:
            if z_score >= self.z_entry:
                # Spread is abnormally high -> Short Y, Long X
                self.events.put(
                    SignalEvent(
                        self.sym_y,
                        dt,
                        "SHORT",
                        strength=1.0,
                        strategy_id=self.strategy_id,
                    )
                )
                self.events.put(
                    SignalEvent(
                        self.sym_x,
                        dt,
                        "LONG",
                        strength=abs(beta),
                        strategy_id=self.strategy_id,
                    )
                )
                self.in_market = "SHORT_SPREAD"
            elif z_score <= -self.z_entry:
                # Spread is abnormally low -> Long Y, Short X
                self.events.put(
                    SignalEvent(
                        self.sym_y,
                        dt,
                        "LONG",
                        strength=1.0,
                        strategy_id=self.strategy_id,
                    )
                )
                self.events.put(
                    SignalEvent(
                        self.sym_x,
                        dt,
                        "SHORT",
                        strength=abs(beta),
                        strategy_id=self.strategy_id,
                    )
                )
                self.in_market = "LONG_SPREAD"

        elif self.in_market in ("LONG_SPREAD", "SHORT_SPREAD"):
            if abs(z_score) <= self.z_exit:
                # Spread reverted to mean -> Exit both legs
                self.events.put(
                    SignalEvent(
                        self.sym_y,
                        dt,
                        "EXIT",
                        strength=1.0,
                        strategy_id=self.strategy_id,
                    )
                )
                self.events.put(
                    SignalEvent(
                        self.sym_x,
                        dt,
                        "EXIT",
                        strength=1.0,
                        strategy_id=self.strategy_id,
                    )
                )
                self.in_market = None
