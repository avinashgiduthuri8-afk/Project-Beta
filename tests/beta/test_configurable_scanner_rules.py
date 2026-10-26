"""Focused contracts for the configurable scanner-rule foundation."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import aiosqlite
import pandas as pd
import pytest

from core.repository.beta_store import BetaStore
from market.models import MarketQuote
from market.symbols import StockSymbol
from scanner.indian_service import IndianScannerService
from scanner.rules import ScreeningRuleConfigError, ScreeningRuleSet


def candles(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    volumes = volumes or [100.0] * len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [price + 1.0 for price in closes],
            "low": [price - 1.0 for price in closes],
            "close": closes,
            "volume": volumes,
        }
    )


def test_parameterized_indicator_rules_compare_latest_values_to_thresholds():
    frame = candles(
        [100.0, 101.0, 102.0, 103.0, 104.0, 106.0],
        [100.0, 100.0, 100.0, 100.0, 100.0, 300.0],
    )
    rules = ScreeningRuleSet.from_mapping(
        {
            "combine": "all",
            "rules": [
                {
                    "indicator": "rsi",
                    "parameters": {"period": 3},
                    "operator": "gte",
                    "threshold": 70,
                },
                {
                    "indicator": "rvol",
                    "parameters": {"period": 3},
                    "operator": "gte",
                    "threshold": 2.5,
                },
                {
                    "indicator": "price_change_pct",
                    "parameters": {"periods": 1},
                    "operator": "gt",
                    "threshold": 1.5,
                },
            ],
        }
    )

    assert rules.matches(frame)

    insufficient_history = ScreeningRuleSet.from_mapping(
        {
            "combine": "all",
            "rules": [
                {
                    "indicator": "rsi",
                    "parameters": {"period": 20},
                    "operator": "gte",
                    "threshold": 70,
                }
            ],
        }
    )
    assert not insufficient_history.matches(frame)


def test_nested_rule_groups_support_all_and_any_combinations():
    frame = candles([100.0, 101.0, 102.0, 103.0, 104.0])
    rules = ScreeningRuleSet.from_mapping(
        {
            "combine": "all",
            "rules": [
                {"indicator": "close", "operator": "gte", "threshold": 104},
                {
                    "combine": "any",
                    "rules": [
                        {"indicator": "volume", "operator": "gt", "threshold": 1_000},
                        {
                            "indicator": "ema",
                            "parameters": {"period": 3},
                            "operator": "lt",
                            "threshold": 104,
                        },
                    ],
                },
            ],
        }
    )

    assert rules.matches(frame)


@pytest.mark.parametrize(
    "config",
    [
        {"combine": "all", "rules": []},
        {"combine": "some", "rules": [{"indicator": "close", "operator": "gt", "threshold": 1}]},
        {"combine": "all", "rules": [{"indicator": "macd", "operator": "gt", "threshold": 0}]},
        {"combine": "all", "rules": [{"indicator": "close", "operator": "crosses", "threshold": 1}]},
        {"combine": "all", "rules": [{"indicator": "close", "operator": "gt", "threshold": math.nan}]},
        {
            "combine": "all",
            "rules": [
                {
                    "indicator": "ema",
                    "parameters": {"period": 0},
                    "operator": "gt",
                    "threshold": 1,
                }
            ],
        },
        {
            "combine": "all",
            "rules": [
                {
                    "indicator": "rsi",
                    "parameters": {"window": 14},
                    "operator": "gt",
                    "threshold": 50,
                }
            ],
        },
        {
            "combine": "all",
            "rules": [
                {"indicator": "close", "operator": "gt", "threshold": 1, "unexpected": True}
            ],
        },
    ],
)
def test_invalid_or_unsupported_rule_configuration_fails_closed(config):
    with pytest.raises(ScreeningRuleConfigError):
        ScreeningRuleSet.from_mapping(config)


def test_missing_or_non_finite_market_values_do_not_match():
    close_rule = ScreeningRuleSet.from_mapping(
        {
            "combine": "all",
            "rules": [{"indicator": "close", "operator": "gt", "threshold": 1}],
        }
    )

    assert not close_rule.matches(pd.DataFrame())
    assert not close_rule.matches(candles([100.0, math.nan]))

    rvol_rule = ScreeningRuleSet.from_mapping(
        {
            "combine": "all",
            "rules": [
                {
                    "indicator": "rvol",
                    "parameters": {"period": 2},
                    "operator": "eq",
                    "threshold": 1,
                }
            ],
        }
    )
    assert not rvol_rule.matches(candles([100.0, 101.0, 102.0], [100.0, math.nan, 100.0]))


@pytest.mark.asyncio
async def test_configured_rules_filter_candidates_before_strategy_evaluation():
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    symbol = StockSymbol.parse("NSE:RELIANCE")
    quote = MarketQuote(symbol, Decimal("100"), now, "TEST", 0)
    raw_candles = [
        {
            "timestamp": int(now.timestamp() * 1000) + index * 60_000,
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1_000_000,
        }
        for index in range(20)
    ]

    class Provider:
        async def get_quote(self, requested_symbol):
            return quote

        async def get_candles(self, requested_symbol, timeframe, limit):
            return raw_candles[-limit:]

    class StrategyMustNotRun:
        def evaluate(self, frame, requested_symbol):
            raise AssertionError("strategy evaluation must follow configurable screening")

    rule_config = {
        "combine": "all",
        "rules": [{"indicator": "close", "operator": "gt", "threshold": 1_000}],
    }
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    store = BetaStore(conn)
    await store.initialize()
    try:
        scanner = IndianScannerService(
            Provider(),
            store,
            [symbol],
            clock=lambda: now,
            strategies={"TEST": StrategyMustNotRun()},
            min_atr_pct=0,
            screening_rules=rule_config,
        )

        result = await scanner.poll()

        assert result["qualified"] == 0
        assert result["rejections"] == [
            {"symbol": "NSE:RELIANCE", "stage": 4, "code": "SCREENING_RULES_NOT_MATCHED"}
        ]
    finally:
        await conn.close()
