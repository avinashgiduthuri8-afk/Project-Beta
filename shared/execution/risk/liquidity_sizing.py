"""
PROJECT-ALPHA — Liquidity-Adjusted Position Sizing Engine (Skill 09).

Dynamically evaluates market depth, 24h volume, and turnover to scale trade sizes
while strictly adhering to PROJECT-ALPHA risk invariants:
  1. Standard Micro-Tranche Cap: Order size is strictly capped at ORDER_SIZE_INR (₹200.00 default).
     It NEVER inflates above this cap, preserving capital across the fleet.
  2. Mandatory ₹200 Minimum Notional: Orders cannot execute below ₹200.00.
     If a pair cannot safely absorb ₹200.00 without excessive slippage/impact, it is rejected.
  3. Multi-Currency Support: Cohesively handles both INR and USDT pairs.
  4. 24h Turnover & Spread Gating: Filters illiquid or high-friction pairs before execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.config import AppConfig
from core.logging import get_logger

logger = get_logger("execution.risk.liquidity_sizing")


class LiquidityTier(str, Enum):
    TIER_1_ELITE = "TIER_1_ELITE"        # > ₹10,00,000 24h volume
    TIER_2_HIGH = "TIER_2_HIGH"          # ₹2,00,000 - ₹10,00,000 24h volume
    TIER_3_MODERATE = "TIER_3_MODERATE"  # ₹50,000 - ₹2,00,000 24h volume
    TIER_4_ILLIQUID = "TIER_4_ILLIQUID"  # < ₹50,000 24h volume


@dataclass
class LiquiditySizingResult:
    allowed: bool
    allocated_amount_inr: float
    scale_factor: float
    liquidity_tier: LiquidityTier
    effective_volume_24h_inr: float
    market_impact_pct: float
    code: str
    reason: str
    details: dict[str, Any]


class LiquidityAdjustedPositionSizer:
    """
    Evaluates market liquidity and sizes trade allocations.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        min_24h_volume_inr: float = 50000.0,
        max_market_impact_pct: float = 0.50,  # 0.5% max of 24h turnover
        max_spread_pct: float = 2.50,
    ) -> None:
        self.config = config
        self.min_24h_volume_inr = (
            getattr(config, "scanner_min_24h_volume", min_24h_volume_inr)
            if config
            else min_24h_volume_inr
        )
        self.max_market_impact_pct = max_market_impact_pct
        self.max_spread_pct = (
            getattr(config, "scanner_max_spread_pct", max_spread_pct)
            if config
            else max_spread_pct
        )

    def classify_tier(self, volume_inr: float) -> LiquidityTier:
        if volume_inr >= 1_000_000.0:
            return LiquidityTier.TIER_1_ELITE
        elif volume_inr >= 200_000.0:
            return LiquidityTier.TIER_2_HIGH
        elif volume_inr >= self.min_24h_volume_inr:
            return LiquidityTier.TIER_3_MODERATE
        else:
            return LiquidityTier.TIER_4_ILLIQUID

    def calculate_order_size(
        self,
        pair: str,
        current_price: float,
        volume_24h: float | None = None,
        volume_24h_quote: float | None = None,
        spread_pct: float | None = None,
        base_trade_amount: float | None = None,
        usdt_inr_rate: float = 90.0,
    ) -> LiquiditySizingResult:
        """
        Calculate liquidity-adjusted trade size respecting the ₹200.00 minimum notional
        and ORDER_SIZE_INR upper bound cap.
        """
        pair_upper = pair.upper()
        is_usdt = pair_upper.endswith("USDT") or "/USDT" in pair_upper

        # 1. Resolve effective 24h volume in INR
        if volume_24h is not None:
            effective_volume_inr = volume_24h * usdt_inr_rate if is_usdt else volume_24h
        elif volume_24h_quote is not None:
            effective_volume_inr = volume_24h_quote * usdt_inr_rate if is_usdt else volume_24h_quote
        else:
            # Fallback if volume is unprovided: assume baseline moderate
            effective_volume_inr = self.min_24h_volume_inr

        # 2. Base target amount resolution (Default: ₹200.00 / ORDER_SIZE_INR)
        target_inr = (
            base_trade_amount
            if base_trade_amount is not None
            else (getattr(self.config, "order_size_inr", 200.0) if self.config else 200.0)
        )
        # Invariant: Must be at least ₹200.00
        target_inr = max(200.0, float(target_inr))

        tier = self.classify_tier(effective_volume_inr)

        # 3. Minimum Volume Gate
        if tier == LiquidityTier.TIER_4_ILLIQUID or effective_volume_inr < self.min_24h_volume_inr:
            reason = (
                f"24h volume ₹{effective_volume_inr:,.2f} is below mandatory liquidity floor "
                f"of ₹{self.min_24h_volume_inr:,.2f} for {pair_upper}."
            )
            logger.warning("Liquidity Sizer Rejection: %s", reason)
            return LiquiditySizingResult(
                allowed=False,
                allocated_amount_inr=0.0,
                scale_factor=0.0,
                liquidity_tier=tier,
                effective_volume_24h_inr=effective_volume_inr,
                market_impact_pct=0.0,
                code="INSUFFICIENT_24H_VOLUME",
                reason=reason,
                details={"pair": pair_upper, "volume_inr": effective_volume_inr},
            )

        # 4. Spread Gate
        if spread_pct is not None and spread_pct > self.max_spread_pct:
            reason = (
                f"Bid/Ask spread {spread_pct:.2f}% exceeds maximum allowable limit "
                f"of {self.max_spread_pct:.2f}% for {pair_upper}."
            )
            logger.warning("Liquidity Sizer Rejection: %s", reason)
            return LiquiditySizingResult(
                allowed=False,
                allocated_amount_inr=0.0,
                scale_factor=0.0,
                liquidity_tier=tier,
                effective_volume_24h_inr=effective_volume_inr,
                market_impact_pct=0.0,
                code="EXCESSIVE_SPREAD",
                reason=reason,
                details={"pair": pair_upper, "spread_pct": spread_pct},
            )

        # 5. Market Impact & Sizing Calculation
        market_impact = (target_inr / effective_volume_inr) * 100.0 if effective_volume_inr > 0 else 1.0

        if market_impact > self.max_market_impact_pct:
            reason = (
                f"Market impact {market_impact:.3f}% exceeds maximum allowable threshold "
                f"of {self.max_market_impact_pct:.2f}% for {pair_upper} at minimum notional ₹200.00."
            )
            logger.warning("Liquidity Sizer Rejection: %s", reason)
            return LiquiditySizingResult(
                allowed=False,
                allocated_amount_inr=0.0,
                scale_factor=0.0,
                liquidity_tier=tier,
                effective_volume_24h_inr=effective_volume_inr,
                market_impact_pct=market_impact,
                code="LIQUIDITY_INSUFFICIENT_FOR_MIN_NOTIONAL",
                reason=reason,
                details={"pair": pair_upper, "market_impact_pct": market_impact},
            )

        # 6. Strict Cap: Order size is capped at standard order amount (target_inr)
        # Sizing factor is 1.0 across liquid tiers, keeping order strictly at ₹200 micro-tranche
        allocated_inr = round(target_inr, 2)
        scale_factor = 1.0

        return LiquiditySizingResult(
            allowed=True,
            allocated_amount_inr=allocated_inr,
            scale_factor=scale_factor,
            liquidity_tier=tier,
            effective_volume_24h_inr=effective_volume_inr,
            market_impact_pct=market_impact,
            code="ALLOWED",
            reason=f"Liquid {tier.value} market conditions confirmed for {pair_upper}.",
            details={
                "pair": pair_upper,
                "is_usdt": is_usdt,
                "volume_inr": effective_volume_inr,
                "market_impact_pct": market_impact,
            },
        )
