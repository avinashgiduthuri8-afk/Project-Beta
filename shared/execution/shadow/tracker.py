"""
PROJECT-ALPHA Shadow Testing & Slippage Divergence Tracker.

Compares simulated fill prices against real order book top-of-book quotes,
computes slippage divergence percentages, flags latency/spread anomalies (> 0.25%),
and records audit logs in shadow_trade_logs.
"""

from __future__ import annotations

from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.production_repo import ProductionRepository

logger = get_logger("execution.shadow.tracker")

# Anomaly threshold: 0.25% slippage divergence
DIVERGENCE_ANOMALY_THRESHOLD_PCT = 0.25


class ShadowDivergenceTracker:
    """Tracks and evaluates fill price divergence between simulated and real order book quotes."""

    def __init__(
        self,
        production_repo: ProductionRepository | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._production_repo = production_repo
        self._bus = bus

    def compute_divergence(
        self,
        simulated_entry_price: float,
        real_orderbook_entry_price: float,
    ) -> float:
        """Compute absolute slippage divergence percentage."""
        if real_orderbook_entry_price <= 0:
            return 0.0
        diff = abs(simulated_entry_price - real_orderbook_entry_price)
        return round((diff / real_orderbook_entry_price) * 100.0, 4)

    async def evaluate_trade_divergence(
        self,
        bot_name: str,
        pair: str,
        simulated_entry_price: float,
        real_orderbook_entry_price: float,
    ) -> dict[str, Any]:
        """
        Evaluate slippage divergence, record audit log, and dispatch alert if anomaly threshold exceeded.
        """
        div_pct = self.compute_divergence(
            simulated_entry_price, real_orderbook_entry_price
        )
        is_anomaly = div_pct > DIVERGENCE_ANOMALY_THRESHOLD_PCT

        record = {
            "bot_name": bot_name.upper(),
            "pair": pair.upper(),
            "simulated_entry_price": simulated_entry_price,
            "real_orderbook_entry_price": real_orderbook_entry_price,
            "slippage_divergence_pct": div_pct,
            "is_anomaly": is_anomaly,
        }

        if self._production_repo:
            log_entry = await self._production_repo.record_shadow_trade_log(
                bot_name=bot_name,
                pair=pair,
                simulated_entry_price=simulated_entry_price,
                real_orderbook_entry_price=real_orderbook_entry_price,
                slippage_divergence_pct=div_pct,
            )
            record["id"] = log_entry["id"]
            record["timestamp"] = log_entry["timestamp"]

        if is_anomaly and self._bus:
            await self._bus.publish(
                EventType.ALERT_GENERATED,
                {
                    "severity": "WARNING",
                    "title": f"Shadow Divergence Anomaly ({bot_name})",
                    "message": f"Slippage divergence of {div_pct:.2f}% exceeds threshold ({DIVERGENCE_ANOMALY_THRESHOLD_PCT}%) for {pair}.",
                    "bot_name": bot_name,
                    "pair": pair,
                    "divergence_pct": div_pct,
                },
            )

        return record
