"""Strict, configuration-driven technical screening rules."""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from shared.research.indicators import (
    compute_atr,
    compute_ema,
    compute_rsi,
    compute_rvol,
    compute_sma,
)


class ScreeningRuleConfigError(ValueError):
    """Raised when a screening-rule configuration is invalid or unsupported."""


_COMPARISONS: Mapping[str, Callable[[float, float], bool]] = MappingProxyType(
    {
        "gt": operator.gt,
        "gte": operator.ge,
        "lt": operator.lt,
        "lte": operator.le,
        "eq": operator.eq,
        "ne": operator.ne,
    }
)
_INDICATOR_PARAMETERS: Mapping[str, tuple[str, int]] = MappingProxyType(
    {
        "close": ("", 0),
        "volume": ("", 0),
        "ema": ("period", 20),
        "sma": ("period", 20),
        "rsi": ("period", 14),
        "atr": ("period", 14),
        "rvol": ("period", 20),
        "price_change_pct": ("periods", 1),
    }
)


def _strict_keys(config: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unexpected = set(config) - allowed
    if unexpected:
        raise ScreeningRuleConfigError(
            f"Unsupported {context} keys: {', '.join(sorted(unexpected))}"
        )


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScreeningRuleConfigError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class IndicatorRule:
    indicator: str
    operator: str
    threshold: float
    parameters: Mapping[str, int]

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "IndicatorRule":
        _strict_keys(config, {"indicator", "operator", "threshold", "parameters"}, "rule")
        indicator = config.get("indicator")
        comparison = config.get("operator")
        if not isinstance(indicator, str) or indicator not in _INDICATOR_PARAMETERS:
            raise ScreeningRuleConfigError(f"Unsupported indicator: {indicator!r}")
        if not isinstance(comparison, str) or comparison not in _COMPARISONS:
            raise ScreeningRuleConfigError(f"Unsupported comparison operator: {comparison!r}")

        threshold = config.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ScreeningRuleConfigError("threshold must be a finite number")
        numeric_threshold = float(threshold)
        if not math.isfinite(numeric_threshold):
            raise ScreeningRuleConfigError("threshold must be a finite number")

        raw_parameters = config.get("parameters", {})
        if not isinstance(raw_parameters, Mapping):
            raise ScreeningRuleConfigError("parameters must be a mapping")
        parameter_name, default_value = _INDICATOR_PARAMETERS[indicator]
        allowed_parameters = {parameter_name} if parameter_name else set()
        _strict_keys(raw_parameters, allowed_parameters, f"{indicator} parameter")
        parameters: dict[str, int] = {}
        if parameter_name:
            parameters[parameter_name] = _positive_integer(
                raw_parameters.get(parameter_name, default_value), parameter_name
            )

        return cls(
            indicator=indicator,
            operator=comparison,
            threshold=numeric_threshold,
            parameters=MappingProxyType(parameters),
        )

    def matches(self, frame: pd.DataFrame) -> bool:
        value = self._latest_value(frame)
        return value is not None and _COMPARISONS[self.operator](value, self.threshold)

    def _latest_value(self, frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        required = {"close"}
        if self.indicator in {"volume", "rvol"}:
            required.add("volume")
        if self.indicator == "atr":
            required.update({"high", "low"})
        if not required.issubset(frame.columns):
            return None

        try:
            close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(close).all():
                return None
            if self.indicator == "close":
                value = close[-1]
            elif self.indicator == "volume":
                volume = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype=float)
                if not np.isfinite(volume).all():
                    return None
                value = volume[-1]
            elif self.indicator == "ema":
                value = compute_ema(close, self.parameters["period"])[-1]
            elif self.indicator == "sma":
                value = compute_sma(close, self.parameters["period"])[-1]
            elif self.indicator == "rsi":
                value = compute_rsi(close, self.parameters["period"])[-1]
            elif self.indicator == "atr":
                high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
                low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
                if not np.isfinite(high).all() or not np.isfinite(low).all():
                    return None
                value = compute_atr(high, low, close, self.parameters["period"])[-1]
            elif self.indicator == "rvol":
                volume = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype=float)
                period = self.parameters["period"]
                if len(volume) <= period or not np.isfinite(volume).all():
                    return None
                value = compute_rvol(volume, period)[-1]
            else:
                periods = self.parameters["periods"]
                if len(close) <= periods or close[-periods - 1] == 0:
                    return None
                value = ((close[-1] / close[-periods - 1]) - 1.0) * 100.0
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

        return float(value) if math.isfinite(float(value)) else None


@dataclass(frozen=True, slots=True)
class RuleGroup:
    combine: str
    rules: tuple[IndicatorRule | "RuleGroup", ...]

    def matches(self, frame: pd.DataFrame) -> bool:
        results = (rule.matches(frame) for rule in self.rules)
        return all(results) if self.combine == "all" else any(results)


def _parse_group(config: Mapping[str, Any], depth: int = 0) -> RuleGroup:
    if depth > 8:
        raise ScreeningRuleConfigError("Rule groups may not be nested more than 8 levels")
    _strict_keys(config, {"combine", "rules"}, "rule group")
    combine = config.get("combine")
    if combine not in {"all", "any"}:
        raise ScreeningRuleConfigError("combine must be 'all' or 'any'")
    raw_rules = config.get("rules")
    if not isinstance(raw_rules, (list, tuple)) or not raw_rules:
        raise ScreeningRuleConfigError("rules must be a non-empty list")

    parsed: list[IndicatorRule | RuleGroup] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, Mapping):
            raise ScreeningRuleConfigError("Each rule must be a mapping")
        if "combine" in raw_rule or "rules" in raw_rule:
            parsed.append(_parse_group(raw_rule, depth + 1))
        else:
            parsed.append(IndicatorRule.from_mapping(raw_rule))
    return RuleGroup(combine=combine, rules=tuple(parsed))


@dataclass(frozen=True, slots=True)
class ScreeningRuleSet:
    root: RuleGroup

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ScreeningRuleSet":
        if not isinstance(config, Mapping):
            raise ScreeningRuleConfigError("Rule set must be a mapping")
        return cls(root=_parse_group(config))

    def matches(self, frame: pd.DataFrame) -> bool:
        if not isinstance(frame, pd.DataFrame):
            return False
        return self.root.matches(frame)


__all__ = [
    "IndicatorRule",
    "RuleGroup",
    "ScreeningRuleConfigError",
    "ScreeningRuleSet",
]
