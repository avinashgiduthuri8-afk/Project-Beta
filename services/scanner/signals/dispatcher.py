"""
PROJECT-ALPHA — Scanner Signal Dispatcher.

Dispatches market intelligence signals from Scanner (Brain) to isolated PAPER and LIVE execution services.
Ensures every emitted signal explicitly carries `mode = PAPER` or `mode = LIVE`.
"""

from typing import Any
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.types import BotMode, Signal

logger = get_logger("scanner.signals.dispatcher")


class SignalDispatcher:
    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus

    async def dispatch(self, signal: Signal) -> dict[str, Any]:
        """
        Dispatch a signal to the sole Beta v1 execution target: PAPER.
        """
        dispatched = {}

        # 1. Dispatch Paper Signal (mode = PAPER)
        paper_signal = self._clone_signal_for_mode(signal, BotMode.PAPER)
        if self.bus:
            await self.bus.publish(
                EventType.SIGNAL_GENERATED,
                self._signal_to_dict(paper_signal),
            )
        dispatched["paper"] = paper_signal.id

        logger.info(
            "Dispatched signal %s to PAPER execution.",
            signal.id,
            extra={"coin": signal.coin, "score": signal.score},
        )
        return dispatched

    def _clone_signal_for_mode(self, signal: Signal, mode: BotMode) -> Signal:
        """Clones a Signal dataclass with an explicit BotMode."""
        return Signal(
            id=f"{signal.id}_{mode.value}",
            coin=signal.coin,
            pair=signal.pair,
            market_state=signal.market_state,
            opportunity_type=signal.opportunity_type,
            priority=signal.priority,
            risk_level=signal.risk_level,
            score=signal.score,
            confidence=signal.confidence,
            coin_class=signal.coin_class,
            mtf_alignment=signal.mtf_alignment,
            generated_at=signal.generated_at,
            expires_at=signal.expires_at,
            mode=mode,
            source_bot=signal.source_bot,
            raw_payload=dict(signal.raw_payload) if signal.raw_payload else {},
            confluence_breakdown=dict(signal.confluence_breakdown)
            if signal.confluence_breakdown
            else {},
        )

    def _signal_to_dict(self, signal: Signal) -> dict[str, Any]:
        """Converts Signal to a serializable dictionary with explicit mode string and full STE contract."""
        raw_p = signal.raw_payload or {}
        price = float(raw_p.get("price") or raw_p.get("entry_price") or raw_p.get("close") or 0.0)
        bot = (
            signal.source_bot
            if signal.source_bot in ("STE", "HDA", "VCP", "BBS", "MTB", "NR7", "PPA", "MRB")
            else raw_p.get("bot", "STE")
        )
        strategy = raw_p.get("strategy") or bot
        strat_name = raw_p.get("strategy_name") or (
            "High Delivery Absorption"
            if strategy == "HDA"
            else (
                "Volatility Contraction Pattern"
                if strategy == "VCP"
                else (
                    "Bollinger Band Squeeze Breakout"
                    if strategy == "BBS"
                    else (
                        "Momentum Trend Breakout"
                        if strategy == "MTB"
                        else (
                            "Narrow Range 7 Squeeze"
                            if strategy == "NR7"
                            else (
                                "Pocket Pivot Accumulation"
                                if strategy == "PPA"
                                else (
                                    "Mean Reversion Bounce"
                                    if strategy == "MRB"
                                    else "SuperTrend ATR Range Expansion"
                                )
                            )
                        )
                    )
                )
            )
        )
        ttl_seconds = (
            int((signal.expires_at - signal.generated_at).total_seconds())
            if signal.expires_at and signal.generated_at
            else 300
        )
        return {
            "id": signal.id,
            "signal_id": signal.id,
            "strategy": strategy,
            "strategy_name": strat_name,
            "bot": bot,
            "coin": signal.coin,
            "pair": signal.pair,
            "timeframe": raw_p.get("timeframe", "1h"),
            "timestamp": signal.generated_at.isoformat(),
            "price": price,
            "entry_price": price,
            "entry": price,
            "stop_loss": raw_p.get("stop_loss"),
            "take_profit": raw_p.get("take_profit"),
            "signal_reason": raw_p.get("signal_reason") or f"{strategy} signal generated",
            "indicators": raw_p.get("indicators") or {},
            "ttl_seconds": ttl_seconds,
            "market_state": (
                signal.market_state.value
                if hasattr(signal.market_state, "value")
                else str(signal.market_state)
            ),
            "opportunity_type": (
                signal.opportunity_type.value
                if hasattr(signal.opportunity_type, "value")
                else str(signal.opportunity_type)
            ),
            "priority": (
                signal.priority.value
                if hasattr(signal.priority, "value")
                else str(signal.priority)
            ),
            "risk_level": (
                signal.risk_level.value
                if hasattr(signal.risk_level, "value")
                else str(signal.risk_level)
            ),
            "score": signal.score,
            "confidence": signal.confidence,
            "coin_class": signal.coin_class,
            "mtf_alignment": signal.mtf_alignment,
            "generated_at": signal.generated_at.isoformat(),
            "expires_at": signal.expires_at.isoformat(),
            "mode": signal.mode.value if isinstance(signal.mode, BotMode) else str(signal.mode),
            "source_bot": signal.source_bot,
            "raw_payload": raw_p,
            "confluence_breakdown": signal.confluence_breakdown,
        }
