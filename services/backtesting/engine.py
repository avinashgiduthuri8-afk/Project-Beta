"""Chronological, in-memory backtesting with conservative long-only fills."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from enum import Enum
from typing import Any, Mapping, Sequence

from market.symbols import StockSymbol
from scanner.validation import MarketDataValidator
from shared.research.indicators import enrich_candles_df
from shared.strategies.base import BaseStockStrategy, StrategySignal


BASIS_POINTS = Decimal("10000")
HUNDRED = Decimal("100")


def _config_decimal(value: object) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("INVALID_BACKTEST_CONFIG") from None
    if not normalized.is_finite():
        raise ValueError("INVALID_BACKTEST_CONFIG")
    return normalized


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_capital: Decimal = Decimal("100000")
    position_size_pct: Decimal = Decimal("100")
    transaction_cost_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    minimum_signal_score: int = 85
    end_of_test: str = "CLOSE"

    def __post_init__(self) -> None:
        for name in (
            "initial_capital",
            "position_size_pct",
            "transaction_cost_bps",
            "slippage_bps",
        ):
            object.__setattr__(self, name, _config_decimal(getattr(self, name)))
        if self.initial_capital <= 0:
            raise ValueError("INVALID_BACKTEST_CONFIG")
        if not Decimal("0") < self.position_size_pct <= HUNDRED:
            raise ValueError("INVALID_BACKTEST_CONFIG")
        if not Decimal("0") <= self.transaction_cost_bps < BASIS_POINTS:
            raise ValueError("INVALID_BACKTEST_CONFIG")
        if not Decimal("0") <= self.slippage_bps < BASIS_POINTS:
            raise ValueError("INVALID_BACKTEST_CONFIG")
        if (
            isinstance(self.minimum_signal_score, bool)
            or not isinstance(self.minimum_signal_score, int)
            or not 0 <= self.minimum_signal_score <= 100
        ):
            raise ValueError("INVALID_BACKTEST_CONFIG")
        policy = str(self.end_of_test).strip().upper()
        if policy not in {"CLOSE", "LEAVE_OPEN"}:
            raise ValueError("INVALID_BACKTEST_CONFIG")
        object.__setattr__(self, "end_of_test", policy)


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    END_OF_TEST = "END_OF_TEST"


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    strategy: str
    symbol: StockSymbol
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    exit_reason: ExitReason
    gross_pnl: Decimal
    transaction_costs: Decimal
    slippage_costs: Decimal
    net_pnl: Decimal


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    drawdown_pct: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_capital: Decimal
    final_equity: Decimal
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    max_drawdown_pct: Decimal
    win_rate_pct: Decimal
    profit_factor: Decimal | None
    net_return_pct: Decimal
    total_transaction_costs: Decimal
    total_slippage_costs: Decimal
    invalid_candle_count: int
    open_position_quantity: int


@dataclass(frozen=True, slots=True)
class _Position:
    signal: StrategySignal
    signal_time: datetime
    entry_time: datetime
    quantity: int
    entry_price: Decimal
    entry_cost: Decimal
    entry_slippage: Decimal


class BacktestEngine:
    """Replays validated candles without databases, brokers, or service state."""

    def __init__(
        self,
        strategies: Mapping[str, BaseStockStrategy],
        config: BacktestConfig | None = None,
    ) -> None:
        self.strategies = dict(strategies)
        self.config = config or BacktestConfig()
        self._validator = MarketDataValidator()

    def run(
        self,
        symbol: StockSymbol,
        candles: Sequence[Mapping[str, Any]] | None,
    ) -> BacktestResult:
        normalized, invalid_count = self._normalize_candles(candles or ())
        cash = self.config.initial_capital
        position: _Position | None = None
        pending: tuple[StrategySignal, datetime] | None = None
        trades: list[BacktestTrade] = []
        raw_equity: list[tuple[datetime, Decimal]] = []

        for index, current in enumerate(normalized):
            timestamp = self._timestamp(current)
            if pending is not None and position is None:
                position, cash = self._open_position(pending, current, cash)
                pending = None

            if position is not None:
                exit_spec = self._risk_exit(position, current)
                if exit_spec is not None:
                    exit_price, reason = exit_spec
                    trade, cash = self._close_position(
                        position, exit_price, timestamp, reason, cash
                    )
                    trades.append(trade)
                    position = None

            equity = cash
            if position is not None:
                equity += Decimal(str(current["close"])) * position.quantity
            raw_equity.append((timestamp, equity))

            if position is None and index < len(normalized) - 1:
                history = enrich_candles_df(normalized[: index + 1])
                signals = self._signals(history, symbol)
                if signals:
                    pending = (signals[0], timestamp)

        if position is not None and self.config.end_of_test == "CLOSE":
            final_candle = normalized[-1]
            exit_base = Decimal(str(final_candle["close"]))
            trade, cash = self._close_position(
                position,
                exit_base,
                self._timestamp(final_candle),
                ExitReason.END_OF_TEST,
                cash,
            )
            trades.append(trade)
            position = None
            raw_equity[-1] = (self._timestamp(final_candle), cash)

        final_equity = raw_equity[-1][1] if raw_equity else cash
        equity_curve = self._equity_curve(raw_equity)
        return self._result(
            final_equity,
            trades,
            equity_curve,
            invalid_count,
            position.quantity if position is not None else 0,
        )

    def _normalize_candles(
        self, candles: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        valid: dict[int, dict[str, Any]] = {}
        invalid_count = 0
        for raw in candles:
            if not isinstance(raw, Mapping):
                invalid_count += 1
                continue
            is_valid, _, normalized = self._validator.validate_candle(dict(raw))
            numeric = (
                normalized.get("open"),
                normalized.get("high"),
                normalized.get("low"),
                normalized.get("close"),
                normalized.get("volume"),
            )
            if not is_valid or not all(
                isinstance(value, (int, float)) and math.isfinite(float(value))
                for value in numeric
            ):
                invalid_count += 1
                continue
            valid[int(normalized["timestamp"])] = normalized
        return [valid[key] for key in sorted(valid)], invalid_count

    def _signals(self, frame: Any, symbol: StockSymbol) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        for strategy in self.strategies.values():
            signal = strategy.evaluate(frame, symbol)
            if (
                signal is not None
                and signal.direction.upper() == "LONG"
                and signal.score >= self.config.minimum_signal_score
                and signal.symbol == symbol
                and self._valid_signal_levels(signal)
            ):
                signals.append(signal)
        return sorted(signals, key=lambda item: (-item.score, item.strategy_name))

    @staticmethod
    def _valid_signal_levels(signal: StrategySignal) -> bool:
        try:
            entry = Decimal(signal.entry_price)
            stop = Decimal(signal.stop_loss)
            target = Decimal(signal.take_profit)
        except (InvalidOperation, TypeError, ValueError):
            return False
        return all(value.is_finite() for value in (entry, stop, target)) and stop < entry < target

    def _open_position(
        self,
        pending: tuple[StrategySignal, datetime],
        candle: Mapping[str, Any],
        cash: Decimal,
    ) -> tuple[_Position | None, Decimal]:
        signal, signal_time = pending
        open_price = Decimal(str(candle["open"]))
        slippage_rate = self.config.slippage_bps / BASIS_POINTS
        cost_rate = self.config.transaction_cost_bps / BASIS_POINTS
        entry_price = open_price * (Decimal("1") + slippage_rate)
        if not signal.stop_loss < entry_price < signal.take_profit:
            return None, cash
        budget = cash * self.config.position_size_pct / HUNDRED
        all_in_per_share = entry_price * (Decimal("1") + cost_rate)
        quantity = int((budget / all_in_per_share).to_integral_value(rounding=ROUND_FLOOR))
        if quantity <= 0:
            return None, cash
        entry_notional = entry_price * quantity
        entry_cost = entry_notional * cost_rate
        debit = entry_notional + entry_cost
        return (
            _Position(
                signal=signal,
                signal_time=signal_time,
                entry_time=self._timestamp(candle),
                quantity=quantity,
                entry_price=entry_price,
                entry_cost=entry_cost,
                entry_slippage=(entry_price - open_price) * quantity,
            ),
            cash - debit,
        )

    @staticmethod
    def _risk_exit(
        position: _Position, candle: Mapping[str, Any]
    ) -> tuple[Decimal, ExitReason] | None:
        open_price = Decimal(str(candle["open"]))
        high = Decimal(str(candle["high"]))
        low = Decimal(str(candle["low"]))
        stop = Decimal(position.signal.stop_loss)
        target = Decimal(position.signal.take_profit)
        if low <= stop:
            return min(open_price, stop), ExitReason.STOP_LOSS
        if high >= target:
            return target, ExitReason.TAKE_PROFIT
        return None

    def _close_position(
        self,
        position: _Position,
        exit_base: Decimal,
        exit_time: datetime,
        reason: ExitReason,
        cash: Decimal,
    ) -> tuple[BacktestTrade, Decimal]:
        slippage_rate = self.config.slippage_bps / BASIS_POINTS
        cost_rate = self.config.transaction_cost_bps / BASIS_POINTS
        exit_price = exit_base * (Decimal("1") - slippage_rate)
        exit_notional = exit_price * position.quantity
        exit_cost = exit_notional * cost_rate
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        transaction_costs = position.entry_cost + exit_cost
        slippage_costs = position.entry_slippage + (
            (exit_base - exit_price) * position.quantity
        )
        trade = BacktestTrade(
            strategy=position.signal.strategy_name,
            symbol=position.signal.symbol,
            signal_time=position.signal_time,
            entry_time=position.entry_time,
            exit_time=exit_time,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_loss=Decimal(position.signal.stop_loss),
            take_profit=Decimal(position.signal.take_profit),
            exit_reason=reason,
            gross_pnl=gross_pnl,
            transaction_costs=transaction_costs,
            slippage_costs=slippage_costs,
            net_pnl=gross_pnl - transaction_costs,
        )
        return trade, cash + exit_notional - exit_cost

    @staticmethod
    def _timestamp(candle: Mapping[str, Any]) -> datetime:
        return datetime.fromtimestamp(int(candle["timestamp"]) / 1000, timezone.utc)

    @staticmethod
    def _equity_curve(
        raw_equity: Sequence[tuple[datetime, Decimal]]
    ) -> tuple[EquityPoint, ...]:
        peak: Decimal | None = None
        points: list[EquityPoint] = []
        for timestamp, equity in raw_equity:
            peak = equity if peak is None else max(peak, equity)
            drawdown = ((peak - equity) / peak * HUNDRED) if peak > 0 else Decimal("0")
            points.append(EquityPoint(timestamp, equity, drawdown))
        return tuple(points)

    def _result(
        self,
        final_equity: Decimal,
        trades: Sequence[BacktestTrade],
        equity_curve: tuple[EquityPoint, ...],
        invalid_count: int,
        open_quantity: int,
    ) -> BacktestResult:
        winners = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
        losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
        win_rate = (
            Decimal(len(winners)) / Decimal(len(trades)) * HUNDRED
            if trades
            else Decimal("0")
        )
        gross_profit = sum(winners, Decimal("0"))
        gross_loss = -sum(losses, Decimal("0"))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        max_drawdown = max(
            (point.drawdown_pct for point in equity_curve), default=Decimal("0")
        )
        return BacktestResult(
            initial_capital=self.config.initial_capital,
            final_equity=final_equity,
            trades=tuple(trades),
            equity_curve=equity_curve,
            max_drawdown_pct=max_drawdown,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            net_return_pct=(
                (final_equity - self.config.initial_capital)
                / self.config.initial_capital
                * HUNDRED
            ),
            total_transaction_costs=sum(
                (trade.transaction_costs for trade in trades), Decimal("0")
            ),
            total_slippage_costs=sum(
                (trade.slippage_costs for trade in trades), Decimal("0")
            ),
            invalid_candle_count=invalid_count,
            open_position_quantity=open_quantity,
        )


__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
    "EquityPoint",
    "ExitReason",
]
