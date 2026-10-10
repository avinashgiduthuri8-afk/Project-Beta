"""
PROJECT-ALPHA LIVE Execution Safety Guards Engine.

Enforces pre-order execution guards for live trading:
  1. Stale Market-Data Guard (timestamp freshness check)
  2. Slippage / Price-Deviation Guard (price jump / deviation limit)
  3. Minimum Liquidity Guard (24h volume / liquidity sanity floor)
  4. Duplicate-Order Guard (cross-strategy single-coin asset lock)
  5. Exchange / API Health Guard (consecutive failure circuit breaker)
  6. Rate-Limit / Request Spacing Guard (request interval spacing)

Emergency Exit Policy:
  Emergency risk-reduction exits (stop loss, circuit breaker, panic close) bypass
  stale data & strict price-slippage checks so emergency risk reduction is never blocked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.logging import get_logger

logger = get_logger("execution.trading.execution_guards")


@dataclass
class GuardCheckResult:
    passed: bool
    guard_name: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class ExecutionSafetyGuards:
    """
    Pre-order execution safety guard evaluation engine.
    """

    def __init__(
        self,
        max_stale_seconds: float = 60.0,
        max_slippage_pct: float = 3.0,
        min_24h_volume: float = 50000.0,
    ) -> None:
        self.max_stale_seconds = max_stale_seconds
        self.max_slippage_pct = max_slippage_pct
        self.min_24h_volume = min_24h_volume
        self._last_request_timestamps: dict[str, float] = {}

    def check_stale_data_guard(
        self,
        data_time: datetime | float,
        is_emergency_exit: bool = False,
    ) -> GuardCheckResult:
        """
        Guard 1: Stale Market Data Check.
        Bypassed if is_emergency_exit=True.
        """
        if is_emergency_exit:
            return GuardCheckResult(
                passed=True,
                guard_name="STALE_DATA_GUARD",
                code="BYPASSED_EMERGENCY",
                message="Stale data check bypassed for emergency exit.",
            )

        now_ts = time.time()
        if isinstance(data_time, datetime):
            if data_time.tzinfo is None:
                data_time = data_time.replace(tzinfo=timezone.utc)
            data_ts = data_time.timestamp()
        else:
            data_ts = float(data_time)

        age_seconds = now_ts - data_ts
        if age_seconds > self.max_stale_seconds:
            msg = f"Stale market data detected: data is {age_seconds:.1f}s old (max allowed: {self.max_stale_seconds}s)"
            logger.warning(msg)
            return GuardCheckResult(
                passed=False,
                guard_name="STALE_DATA_GUARD",
                code="STALE_DATA_REJECTED",
                message=msg,
                details={
                    "age_seconds": age_seconds,
                    "max_allowed": self.max_stale_seconds,
                },
            )

        return GuardCheckResult(
            passed=True,
            guard_name="STALE_DATA_GUARD",
            code="PASSED",
            message=f"Data age {age_seconds:.1f}s is fresh.",
            details={"age_seconds": age_seconds},
        )

    def check_slippage_guard(
        self,
        order_price: float,
        ticker_price: float,
        is_emergency_exit: bool = False,
    ) -> GuardCheckResult:
        """
        Guard 2: Slippage / Price Deviation Guard.
        Bypassed if is_emergency_exit=True.
        """
        if is_emergency_exit:
            return GuardCheckResult(
                passed=True,
                guard_name="SLIPPAGE_GUARD",
                code="BYPASSED_EMERGENCY",
                message="Slippage check bypassed for emergency exit.",
            )

        if order_price <= 0.0 or ticker_price <= 0.0:
            return GuardCheckResult(
                passed=False,
                guard_name="SLIPPAGE_GUARD",
                code="INVALID_PRICE",
                message="Invalid order or ticker price for slippage calculation.",
            )

        slippage_pct = (abs(order_price - ticker_price) / ticker_price) * 100.0
        if slippage_pct > self.max_slippage_pct:
            msg = f"Price slippage {slippage_pct:.2f}% exceeds maximum allowed limit {self.max_slippage_pct}%"
            logger.warning(msg)
            return GuardCheckResult(
                passed=False,
                guard_name="SLIPPAGE_GUARD",
                code="SLIPPAGE_REJECTED",
                message=msg,
                details={
                    "slippage_pct": slippage_pct,
                    "max_allowed": self.max_slippage_pct,
                },
            )

        return GuardCheckResult(
            passed=True,
            guard_name="SLIPPAGE_GUARD",
            code="PASSED",
            message=f"Slippage {slippage_pct:.2f}% within bounds.",
            details={"slippage_pct": slippage_pct},
        )

    def check_liquidity_guard(
        self,
        pair: str,
        volume_24h: float,
    ) -> GuardCheckResult:
        """
        Guard 3: Minimum Liquidity Guard.
        """
        if volume_24h < self.min_24h_volume:
            msg = f"Insufficient 24h liquidity for {pair}: ₹{volume_24h:,.2f} < minimum ₹{self.min_24h_volume:,.2f}"
            logger.warning(msg)
            return GuardCheckResult(
                passed=False,
                guard_name="LIQUIDITY_GUARD",
                code="INSUFFICIENT_LIQUIDITY",
                message=msg,
                details={"volume_24h": volume_24h, "min_required": self.min_24h_volume},
            )

        return GuardCheckResult(
            passed=True,
            guard_name="LIQUIDITY_GUARD",
            code="PASSED",
            message=f"Liquidity ₹{volume_24h:,.2f} meets minimum threshold.",
        )

    def check_duplicate_order_guard(
        self,
        coin: str,
        active_coins: list[str],
    ) -> GuardCheckResult:
        """
        Guard 4: Duplicate Order / Single Coin Lock Guard.
        """
        coin_upper = coin.upper().split("/")[0].split("_")[0]
        active_bases = [c.upper().split("/")[0].split("_")[0] for c in active_coins]

        if coin_upper in active_bases:
            msg = f"Duplicate order rejected by Asset Lock Guard: active position already exists for {coin_upper}"
            logger.warning(msg)
            return GuardCheckResult(
                passed=False,
                guard_name="DUPLICATE_ORDER_GUARD",
                code="DUPLICATE_POSITION_EXISTS",
                message=msg,
                details={"coin": coin_upper},
            )

        return GuardCheckResult(
            passed=True,
            guard_name="DUPLICATE_ORDER_GUARD",
            code="PASSED",
            message=f"No duplicate active position for {coin_upper}.",
        )

    def check_api_health_guard(
        self,
        consecutive_failures: int,
        max_allowed_failures: int = 3,
    ) -> GuardCheckResult:
        """
        Guard 5: Exchange / API Health Guard & Consecutive Failure Breaker.
        """
        if consecutive_failures >= max_allowed_failures:
            msg = f"Exchange API Health Guard tripped: {consecutive_failures} consecutive API failures (max: {max_allowed_failures})"
            logger.error(msg)
            return GuardCheckResult(
                passed=False,
                guard_name="API_HEALTH_GUARD",
                code="API_CIRCUIT_OPEN",
                message=msg,
                details={"consecutive_failures": consecutive_failures},
            )

        return GuardCheckResult(
            passed=True,
            guard_name="API_HEALTH_GUARD",
            code="PASSED",
            message=f"API health normal ({consecutive_failures} failures).",
        )

    def check_rate_limit_guard(
        self,
        bot_key: str,
        min_interval_ms: float = 200.0,
    ) -> GuardCheckResult:
        """
        Guard 6: Rate-Limit Spacing Guard.
        """
        now_ts = time.time() * 1000.0
        last_ts = self._last_request_timestamps.get(bot_key, 0.0)
        elapsed = now_ts - last_ts

        if elapsed < min_interval_ms:
            msg = f"Rate-limit spacing guard: elapsed {elapsed:.0f}ms < min interval {min_interval_ms:.0f}ms"
            logger.warning(msg)
            return GuardCheckResult(
                passed=False,
                guard_name="RATE_LIMIT_GUARD",
                code="RATE_LIMIT_EXCEEDED",
                message=msg,
                details={"elapsed_ms": elapsed, "min_interval_ms": min_interval_ms},
            )

        self._last_request_timestamps[bot_key] = now_ts
        return GuardCheckResult(
            passed=True,
            guard_name="RATE_LIMIT_GUARD",
            code="PASSED",
            message="Rate limit spacing verified.",
        )

    def evaluate_all_guards(
        self,
        order_price: float,
        ticker_price: float,
        data_time: datetime | float,
        volume_24h: float,
        coin: str,
        active_coins: list[str],
        consecutive_failures: int = 0,
        bot_key: str = "STE",
        is_emergency_exit: bool = False,
    ) -> tuple[bool, list[GuardCheckResult]]:
        """
        Evaluate all live execution safety guards in sequence.
        Returns (all_passed, list_of_check_results).
        """
        checks = [
            self.check_stale_data_guard(data_time, is_emergency_exit=is_emergency_exit),
            self.check_slippage_guard(
                order_price, ticker_price, is_emergency_exit=is_emergency_exit
            ),
            self.check_liquidity_guard(coin, volume_24h),
            self.check_duplicate_order_guard(coin, active_coins),
            self.check_api_health_guard(consecutive_failures),
            self.check_rate_limit_guard(bot_key),
        ]

        all_passed = all(c.passed for c in checks)
        return all_passed, checks
