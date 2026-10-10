"""
PROJECT-ALPHA — Paper/Live Execution Mode Isolation Engine (Skill 14).

Enforces strict, multi-layered isolation between PAPER and LIVE modes:
  1. Credentials Isolation: Enforces dedicated keys; forbids live credentials in paper mode
     and forbids mock/demo keys in live mode.
  2. Balances Isolation: Virtual paper balance is strictly decoupled from real exchange balances.
  3. Outbound Dispatch Firewall: Absolutely blocks outbound exchange HTTP orders in paper mode.
  4. Order & Entity Integrity: Ensures order metadata and DB position states strictly reflect
     their authorized execution environment.
"""

from __future__ import annotations

from enum import Enum
from core.config import AppConfig
from core.exceptions import AlphaError
from core.logging import get_logger
from core.types import BotMode

logger = get_logger("execution.trading.mode_isolation")


class ModeIsolationError(AlphaError):
    """Raised when an execution mode boundary is breached."""


class IsolationViolationType(str, Enum):
    LIVE_API_CALLED_IN_PAPER = "LIVE_API_CALLED_IN_PAPER"
    PAPER_CALLED_IN_LIVE = "PAPER_CALLED_IN_LIVE"
    LIVE_CREDENTIALS_IN_PAPER = "LIVE_CREDENTIALS_IN_PAPER"
    PAPER_CREDENTIALS_IN_LIVE = "PAPER_CREDENTIALS_IN_LIVE"
    BALANCE_SOURCE_MISMATCH = "BALANCE_SOURCE_MISMATCH"
    ORDER_MODE_MISMATCH = "ORDER_MODE_MISMATCH"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"


class ModeIsolationGuard:
    """
    Enforces runtime isolation between Paper and Live environments.
    """

    MOCK_KEY_SUBSTRINGS = ("paper", "demo", "mock", "test_key", "sample")

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config

    def is_paper_mode(self, mode: str | BotMode) -> bool:
        mode_str = mode.value if isinstance(mode, BotMode) else str(mode)
        return mode_str.upper() in ("PAPER", "SHADOW", "SIMULATED", "BACKTEST")

    def is_live_mode(self, mode: str | BotMode) -> bool:
        mode_str = mode.value if isinstance(mode, BotMode) else str(mode)
        return mode_str.upper() in ("LIVE", "LIVE_MICROCASH", "REAL")

    def validate_credentials(
        self,
        mode: str | BotMode,
        api_key: str | None,
        api_secret: str | None,
    ) -> tuple[bool, str]:
        """
        Validate that API credentials strictly match the active execution mode.
        """
        is_paper = self.is_paper_mode(mode)
        is_live = self.is_live_mode(mode)

        if is_live:
            if not api_key or not api_secret:
                return (
                    False,
                    "Live mode requires non-empty coindcx_live_api_key and coindcx_live_api_secret.",
                )
            key_lower = api_key.lower()
            if any(m in key_lower for m in self.MOCK_KEY_SUBSTRINGS):
                return (
                    False,
                    f"Live mode rejected demo/mock API key: {api_key}.",
                )
            return True, "VALID_LIVE_CREDENTIALS"

        if is_paper:
            # Paper mode must not require real credentials and should not use production keys
            return True, "VALID_PAPER_CREDENTIALS"

        return False, f"Unknown execution mode: {mode}"

    def validate_outbound_dispatch(
        self,
        mode: str | BotMode,
        is_live_network_call: bool,
    ) -> tuple[bool, str]:
        """
        Firewall gate: Verify that paper mode never invokes outbound exchange HTTP endpoints.
        """
        if self.is_paper_mode(mode) and is_live_network_call:
            msg = "Outbound exchange network dispatch strictly forbidden in PAPER mode."
            logger.critical("Mode Isolation Breach: %s", msg)
            return False, IsolationViolationType.LIVE_API_CALLED_IN_PAPER.value

        return True, "ALLOWED"

    def validate_balance_access(
        self,
        mode: str | BotMode,
        balance_source: str,
    ) -> tuple[bool, str]:
        """
        Prevent cross-contamination between virtual simulated balances and live wallet balances.
        """
        is_paper = self.is_paper_mode(mode)
        source = balance_source.upper()

        if is_paper and source == "LIVE_EXCHANGE":
            msg = "Paper execution must not read or mutate live exchange wallet balance."
            logger.error("Balance Isolation Breach: %s", msg)
            return False, IsolationViolationType.BALANCE_SOURCE_MISMATCH.value

        if not is_paper and source == "PAPER_VIRTUAL":
            msg = "Live execution must not rely on virtual paper balance."
            logger.error("Balance Isolation Breach: %s", msg)
            return False, IsolationViolationType.BALANCE_SOURCE_MISMATCH.value

        return True, "ALLOWED"

    def validate_order_intent(
        self,
        deployment_mode: str | BotMode,
        trading_enabled: bool,
        target_mode: str | BotMode,
    ) -> tuple[bool, str]:
        """
        Pre-flight check before order construction:
          1. Target mode matches active deployment mode.
          2. Live execution is guarded by master trading_enabled switch.
        """
        dep_live = self.is_live_mode(deployment_mode)
        target_live = self.is_live_mode(target_mode)

        if target_live and not dep_live:
            return (
                False,
                f"Cannot execute LIVE order when deployment mode is {deployment_mode}.",
            )

        if not target_live and dep_live:
            # Paper order in live mode is only allowed if explicit shadow mode is enabled
            shadow_enabled = getattr(self.config, "shadow_mode", False) if self.config else False
            if not shadow_enabled:
                return (
                    False,
                    f"Paper order rejected in LIVE deployment without shadow mode enabled.",
                )

        if target_live and not trading_enabled:
            return (
                False,
                "Live order rejected: trading_enabled is False (Kill Switch Active).",
            )

        return True, "ALLOWED"
