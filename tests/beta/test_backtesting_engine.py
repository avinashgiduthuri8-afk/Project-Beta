"""Deterministic contracts for the isolated historical backtesting engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtesting.engine import BacktestConfig, BacktestEngine, ExitReason
from market.symbols import StockSymbol
from shared.strategies.base import StrategySignal
from shared.strategies.registry import get_active_strategies


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = StockSymbol.parse("NSE:TEST")


def candle(
    index: int,
    *,
    open_price: object = 100,
    high: object = 101,
    low: object = 99,
    close: object = 100,
    volume: object = 1_000,
    gap_minutes: int = 5,
) -> dict:
    return {
        "timestamp": int((START + timedelta(minutes=index * gap_minutes)).timestamp() * 1000),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class ScriptedStrategy:
    name = "SCRIPTED"

    def __init__(self, signals: dict[int, tuple[Decimal, Decimal]]) -> None:
        self.signals = signals
        self.seen_lengths: list[int] = []

    def evaluate(self, frame, symbol):
        length = len(frame)
        self.seen_lengths.append(length)
        levels = self.signals.get(length)
        if levels is None:
            return None
        stop_loss, take_profit = levels
        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            entry_price=Decimal(str(frame.iloc[-1]["close"])),
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=90,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"initial_capital": 0},
        {"position_size_pct": 0},
        {"position_size_pct": 101},
        {"transaction_cost_bps": -1},
        {"slippage_bps": "NaN"},
        {"minimum_signal_score": 101},
        {"end_of_test": "UNKNOWN"},
    ],
)
def test_invalid_backtest_configuration_is_rejected(overrides):
    with pytest.raises(ValueError, match="INVALID_BACKTEST_CONFIG"):
        BacktestConfig(**overrides)


def test_signal_uses_only_revealed_candles_and_enters_at_next_open():
    strategy = ScriptedStrategy({1: (Decimal("90"), Decimal("120"))})
    candles = [
        candle(2, open_price=114, high=116, low=113, close=115, gap_minutes=30),
        candle(0, open_price=99, high=101, low=98, close=100),
        candle(1, open_price=110, high=112, low=109, close=111, gap_minutes=30),
    ]

    result = BacktestEngine({strategy.name: strategy}).run(SYMBOL, candles)

    assert strategy.seen_lengths == [1]
    assert len(result.trades) == 1
    assert result.trades[0].signal_time == START
    assert result.trades[0].entry_time == START + timedelta(minutes=30)
    assert result.trades[0].entry_price == Decimal("110")
    assert result.trades[0].exit_price == Decimal("115")
    assert result.trades[0].exit_reason is ExitReason.END_OF_TEST


@pytest.mark.parametrize(
    ("high", "low", "expected_price", "expected_reason"),
    [
        (Decimal("104"), Decimal("94"), Decimal("95"), ExitReason.STOP_LOSS),
        (Decimal("106"), Decimal("96"), Decimal("105"), ExitReason.TAKE_PROFIT),
        (Decimal("106"), Decimal("94"), Decimal("95"), ExitReason.STOP_LOSS),
    ],
)
def test_intrabar_risk_exits_use_conservative_level_ordering(
    high, low, expected_price, expected_reason
):
    strategy = ScriptedStrategy({1: (Decimal("95"), Decimal("105"))})
    candles = [
        candle(0, close=100),
        candle(1, open_price=100, high=high, low=low, close=100),
    ]

    result = BacktestEngine({strategy.name: strategy}).run(SYMBOL, candles)

    assert result.trades[0].exit_price == expected_price
    assert result.trades[0].exit_reason is expected_reason


def test_position_sizing_fees_slippage_and_end_close_are_accounted_for():
    strategy = ScriptedStrategy({1: (Decimal("50"), Decimal("200"))})
    config = BacktestConfig(
        initial_capital=Decimal("1000"),
        position_size_pct=Decimal("100"),
        transaction_cost_bps=Decimal("100"),
        slippage_bps=Decimal("100"),
    )
    candles = [
        candle(0, close=100),
        candle(1, open_price=100, high=111, low=99, close=110),
    ]

    result = BacktestEngine({strategy.name: strategy}, config).run(SYMBOL, candles)
    trade = result.trades[0]

    assert trade.quantity == 9
    assert trade.entry_price == Decimal("101.00")
    assert trade.exit_price == Decimal("108.90")
    assert trade.transaction_costs == Decimal("18.8910")
    assert trade.slippage_costs == Decimal("18.90")
    assert trade.net_pnl == Decimal("52.2090")
    assert result.final_equity == Decimal("1052.2090")
    assert result.net_return_pct == Decimal("5.220900")
    assert result.total_transaction_costs == Decimal("18.8910")


def test_metrics_track_win_rate_profit_factor_and_drawdown():
    strategy = ScriptedStrategy(
        {
            1: (Decimal("95"), Decimal("200")),
            3: (Decimal("90"), Decimal("110")),
        }
    )
    candles = [
        candle(0, close=100),
        candle(1, open_price=100, high=101, low=94, close=95),
        candle(2, open_price=100, high=101, low=99, close=100),
        candle(3, open_price=100, high=111, low=99, close=110),
    ]

    result = BacktestEngine(
        {strategy.name: strategy}, BacktestConfig(initial_capital=Decimal("1000"))
    ).run(SYMBOL, candles)

    assert [trade.net_pnl for trade in result.trades] == [Decimal("-50"), Decimal("90")]
    assert result.win_rate_pct == Decimal("50")
    assert result.profit_factor == Decimal("1.8")
    assert result.max_drawdown_pct == Decimal("5.00")
    assert result.net_return_pct == Decimal("4.00")
    assert result.final_equity == Decimal("1040")


def test_empty_invalid_and_insufficient_datasets_fail_safely():
    engine = BacktestEngine(get_active_strategies())

    empty = engine.run(SYMBOL, [])
    invalid = engine.run(
        SYMBOL,
        [
            candle(0, open_price=0, high=1, low=0, close=0),
            {"timestamp": 0, "open": 100, "high": 101, "low": 99, "close": 100},
        ],
    )
    insufficient = engine.run(
        SYMBOL,
        [candle(index, close=100 + index) for index in range(10)],
    )

    assert empty.trades == ()
    assert empty.final_equity == empty.initial_capital
    assert invalid.trades == ()
    assert invalid.invalid_candle_count == 2
    assert insufficient.trades == ()
    assert insufficient.final_equity == insufficient.initial_capital


def test_end_of_test_can_leave_a_position_marked_but_unrealized():
    strategy = ScriptedStrategy({1: (Decimal("90"), Decimal("120"))})
    result = BacktestEngine(
        {strategy.name: strategy}, BacktestConfig(end_of_test="LEAVE_OPEN")
    ).run(
        SYMBOL,
        [candle(0, close=100), candle(1, open_price=100, high=111, low=99, close=110)],
    )

    assert result.trades == ()
    assert result.open_position_quantity > 0
    assert result.final_equity > result.initial_capital
