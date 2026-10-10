"""
PROJECT-ALPHA — Correlation-Aware Exposure Limits Engine (Skill 10).

Prevents systemic portfolio collapse caused by correlated crypto sector sell-offs
by enforcing cluster-level and portfolio-level risk caps:
  1. Crypto Sector & Beta Classification: Maps coins into distinct risk clusters
     (BTC Beta, Layer 1s, DeFi, Memes, AI/Data, etc.).
  2. Cluster Position Limits: Caps simultaneous positions within a single sector (default: max 2).
  3. Cluster Capital Ceiling: Restricts total capital concentration within any single cluster.
  4. Portfolio Concentration Protection: Flags overexposure to high-beta altcoin baskets.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from core.config import AppConfig
from core.logging import get_logger
from core.types import BotName, RiskDecision
from execution.trading.precision_rules import extract_base_coin

logger = get_logger("execution.risk.correlation_limits")


class CryptoCluster(str, Enum):
    BTC_BETA = "BTC_BETA"
    ETH_ECOSYSTEM = "ETH_ECOSYSTEM"
    LAYER_1 = "LAYER_1"
    DEFI = "DEFI"
    MEME = "MEME"
    AI_DATA = "AI_DATA"
    EXCHANGE_TOKENS = "EXCHANGE_TOKENS"
    GENERAL_ALT = "GENERAL_ALT"


# Sector mapping for popular tradeable crypto assets on CoinDCX
CLUSTER_MAP: dict[str, CryptoCluster] = {
    # Bitcoin & Direct Beta
    "BTC": CryptoCluster.BTC_BETA,
    "WBTC": CryptoCluster.BTC_BETA,
    "BCH": CryptoCluster.BTC_BETA,
    "BSV": CryptoCluster.BTC_BETA,
    "STX": CryptoCluster.BTC_BETA,
    # Ethereum & Layer 2s
    "ETH": CryptoCluster.ETH_ECOSYSTEM,
    "WETH": CryptoCluster.ETH_ECOSYSTEM,
    "ARB": CryptoCluster.ETH_ECOSYSTEM,
    "OP": CryptoCluster.ETH_ECOSYSTEM,
    "MATIC": CryptoCluster.ETH_ECOSYSTEM,
    "POL": CryptoCluster.ETH_ECOSYSTEM,
    "LDO": CryptoCluster.ETH_ECOSYSTEM,
    "SSV": CryptoCluster.ETH_ECOSYSTEM,
    "MNT": CryptoCluster.ETH_ECOSYSTEM,
    "METIS": CryptoCluster.ETH_ECOSYSTEM,
    # Alternative Layer 1s
    "SOL": CryptoCluster.LAYER_1,
    "ADA": CryptoCluster.LAYER_1,
    "AVAX": CryptoCluster.LAYER_1,
    "DOT": CryptoCluster.LAYER_1,
    "NEAR": CryptoCluster.LAYER_1,
    "ATOM": CryptoCluster.LAYER_1,
    "SUI": CryptoCluster.LAYER_1,
    "APT": CryptoCluster.LAYER_1,
    "FTM": CryptoCluster.LAYER_1,
    "ALGO": CryptoCluster.LAYER_1,
    "TON": CryptoCluster.LAYER_1,
    "SEI": CryptoCluster.LAYER_1,
    "TIA": CryptoCluster.LAYER_1,
    "INJ": CryptoCluster.LAYER_1,
    "KAS": CryptoCluster.LAYER_1,
    # Decentralized Finance (DeFi)
    "UNI": CryptoCluster.DEFI,
    "AAVE": CryptoCluster.DEFI,
    "LINK": CryptoCluster.DEFI,
    "MKR": CryptoCluster.DEFI,
    "SNX": CryptoCluster.DEFI,
    "CRV": CryptoCluster.DEFI,
    "SUSHI": CryptoCluster.DEFI,
    "COMP": CryptoCluster.DEFI,
    "CAKE": CryptoCluster.DEFI,
    "DYDX": CryptoCluster.DEFI,
    "RUNE": CryptoCluster.DEFI,
    "PENDLE": CryptoCluster.DEFI,
    # Memecoins
    "DOGE": CryptoCluster.MEME,
    "SHIB": CryptoCluster.MEME,
    "PEPE": CryptoCluster.MEME,
    "BONK": CryptoCluster.MEME,
    "FLOKI": CryptoCluster.MEME,
    "WIF": CryptoCluster.MEME,
    "MEME": CryptoCluster.MEME,
    "BOME": CryptoCluster.MEME,
    "MEW": CryptoCluster.MEME,
    "POPCAT": CryptoCluster.MEME,
    # AI & Compute
    "RENDER": CryptoCluster.AI_DATA,
    "FET": CryptoCluster.AI_DATA,
    "TAO": CryptoCluster.AI_DATA,
    "GRT": CryptoCluster.AI_DATA,
    "OCEAN": CryptoCluster.AI_DATA,
    "AGIX": CryptoCluster.AI_DATA,
    "FIL": CryptoCluster.AI_DATA,
    "AR": CryptoCluster.AI_DATA,
    "THETA": CryptoCluster.AI_DATA,
    # Exchange Tokens
    "BNB": CryptoCluster.EXCHANGE_TOKENS,
    "OKB": CryptoCluster.EXCHANGE_TOKENS,
    "KCS": CryptoCluster.EXCHANGE_TOKENS,
    "GT": CryptoCluster.EXCHANGE_TOKENS,
}


class CorrelationExposureGuard:
    """
    Monitors and bounds exposure across correlated asset clusters.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        max_positions_per_cluster: int = 2,
        max_cluster_capital_inr: float = 600.0,
        max_cluster_share_pct: float = 40.0,
    ) -> None:
        self.config = config
        self.max_positions_per_cluster = max_positions_per_cluster
        self.max_cluster_capital_inr = max_cluster_capital_inr
        self.max_cluster_share_pct = max_cluster_share_pct

    def classify_coin(self, coin_or_pair: str) -> CryptoCluster:
        """Identify crypto cluster for given coin or trading pair."""
        base = extract_base_coin(coin_or_pair)
        if not base:
            return CryptoCluster.GENERAL_ALT
        return CLUSTER_MAP.get(base.upper(), CryptoCluster.GENERAL_ALT)

    def get_cluster_breakdown(
        self, active_positions: list[Any] | None
    ) -> dict[str, dict[str, Any]]:
        """Compute active position count and capital concentration per cluster."""
        breakdown: dict[str, dict[str, Any]] = {
            c.value: {"count": 0, "capital_inr": 0.0, "coins": []}
            for c in CryptoCluster
        }

        if not active_positions:
            return breakdown

        for pos in active_positions:
            coin = (
                getattr(pos, "coin", None)
                or (pos.get("coin") if isinstance(pos, dict) else "")
                or getattr(pos, "pair", None)
                or (pos.get("pair") if isinstance(pos, dict) else "")
                or ""
            )
            base = extract_base_coin(coin)
            cluster = self.classify_coin(base)

            # Amount resolution
            entry_px = float(
                getattr(pos, "entry_price", None)
                or (pos.get("entry_price") if isinstance(pos, dict) else 0.0)
                or 0.0
            )
            qty = float(
                getattr(pos, "qty", None)
                or (pos.get("qty") if isinstance(pos, dict) else 0.0)
                or 0.0
            )
            amount = entry_px * qty
            if amount <= 0.0:
                amount = float(
                    getattr(pos, "amount", None)
                    or (pos.get("amount") if isinstance(pos, dict) else 200.0)
                    or 200.0
                )

            c_dict = breakdown[cluster.value]
            c_dict["count"] += 1
            c_dict["capital_inr"] += round(amount, 2)
            if base and base not in c_dict["coins"]:
                c_dict["coins"].append(base)

        return breakdown

    def check_exposure(
        self,
        bot: BotName,
        candidate_coin: str,
        requested_amount: float,
        active_positions: list[Any] | None = None,
        total_portfolio_capital: float | None = None,
    ) -> RiskDecision:
        """
        Evaluate whether opening this candidate position violates correlation/cluster limits.
        """
        t0 = time.perf_counter()
        base_coin = extract_base_coin(candidate_coin)
        cluster = self.classify_coin(base_coin)

        breakdown = self.get_cluster_breakdown(active_positions)
        cluster_info = breakdown[cluster.value]
        current_count = cluster_info["count"]
        current_capital = cluster_info["capital_inr"]

        # 1. Cluster Position Count Limit
        if current_count >= self.max_positions_per_cluster:
            ms = (time.perf_counter() - t0) * 1000.0
            reason = (
                f"Cluster {cluster.value} limit reached: {current_count} active positions "
                f"({', '.join(cluster_info['coins'])}) already open (max {self.max_positions_per_cluster})."
            )
            logger.warning("Correlation Limit Breach: %s", reason)
            return RiskDecision(
                allowed=False,
                code="BLOCKED_CORRELATION_LIMIT",
                reason=reason,
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        # 2. Cluster Capital Ceiling
        if (current_capital + requested_amount) > self.max_cluster_capital_inr:
            ms = (time.perf_counter() - t0) * 1000.0
            reason = (
                f"Cluster {cluster.value} capital ceiling exceeded: ₹{current_capital + requested_amount:.2f} "
                f"would exceed maximum allowed ₹{self.max_cluster_capital_inr:.2f} for sector."
            )
            logger.warning("Correlation Limit Breach: %s", reason)
            return RiskDecision(
                allowed=False,
                code="BLOCKED_CORRELATION_LIMIT",
                reason=reason,
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        # 3. Portfolio Share Limit (if total capital is bounded)
        if total_portfolio_capital and total_portfolio_capital > 0:
            projected_share = ((current_capital + requested_amount) / total_portfolio_capital) * 100.0
            if projected_share > self.max_cluster_share_pct:
                ms = (time.perf_counter() - t0) * 1000.0
                reason = (
                    f"Cluster {cluster.value} portfolio share {projected_share:.1f}% "
                    f"exceeds ceiling of {self.max_cluster_share_pct:.1f}%."
                )
                logger.warning("Correlation Limit Breach: %s", reason)
                return RiskDecision(
                    allowed=False,
                    code="BLOCKED_CORRELATION_LIMIT",
                    reason=reason,
                    bot=bot,
                    amount=requested_amount,
                    adjusted_amount=0.0,
                    check_ms=round(ms, 2),
                )

        ms = (time.perf_counter() - t0) * 1000.0
        return RiskDecision(
            allowed=True,
            code="ALLOWED",
            reason=f"Cluster {cluster.value} exposure verified ({current_count}/{self.max_positions_per_cluster} positions).",
            bot=bot,
            amount=requested_amount,
            adjusted_amount=requested_amount,
            check_ms=round(ms, 2),
        )
