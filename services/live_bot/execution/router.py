"""
PROJECT-ALPHA — Live Execution Router & Safeguards.

Handles signal validation, risk checks, live order execution, and position state.
Strictly rejects any signal or order where mode != LIVE.
Never accesses PAPER trading state or Paper DB.
"""

from typing import Any
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from execution.auto_trader import AutoTradeRouter
from shared.mode_guard import ModeIsolationError, validate_execution_mode

logger = get_logger("live.execution.router")


class LiveExecutionRouter:
    """
    Live Execution Router.
    Validates live signals and routes them to real CoinDCX order execution handlers.
    """

    def __init__(
        self,
        bus: EventBus,
        auto_trader: AutoTradeRouter,
    ) -> None:
        self.bus = bus
        self.auto_trader = auto_trader

    async def handle_signal_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        EventBus callback for SIGNAL_GENERATED.
        Enforces live mode isolation safety check.
        """
        try:
            # Hard Safeguard Rule 8: Live execution layer MUST reject mode != LIVE
            validate_execution_mode(payload, expected_mode="LIVE", component="LiveExecutionRouter")
        except ModeIsolationError as err:
            logger.warning("LiveExecutionRouter rejected signal: %s", err)
            return {
                "success": False,
                "rejected": True,
                "reason": str(err),
            }

        # Process signal in live execution mode (dry_run=False if trading_enabled, else dry_run=True for safety)
        logger.info("Live execution router processing signal: %s", payload.get("id"))
        result = await self.auto_trader.handle_signal(payload, dry_run=self.auto_trader.dry_run)
        return result
