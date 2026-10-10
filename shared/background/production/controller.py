"""
core/production_service/controller.py — Production Controller & Emergency Kill-Switch.
background/production/controller.py — Production Controller & Emergency Kill-Switch.

Manages:
1. Dynamic mode switching between SHADOW, PAPER, and LIVE_MICROCASH with persistence.
2. Global emergency kill-switch tripping the circuit breaker and halting orders.
3. System resume procedure with database integrity checks and breaker re-arming.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig, get_config
from core.logging import get_logger

logger = get_logger("background.production.controller")

MINIMUM_NOTIONAL_INR: float = 200.0
# Compatibility exports for older status consumers.  These maps are
# intentionally empty: capital allocation belongs to the configured shared
# pool and risk engine, not hard-coded bot/subaccount ceilings.
WALLET_LIMITS_INR: dict[str, float] = {}
MICRO_ORDER_CAPS_INR: dict[str, float] = {}


class DeploymentMode(str, Enum):
    """Execution deployment modes (PAPER simulation or LIVE microcash)."""

    PAPER = "PAPER"
    LIVE = "LIVE"
    LIVE_MICROCASH = "LIVE_MICROCASH"


class ProductionController:
    """
    Central controller orchestrating deployment mode transitions, emergency stops,
    and system-wide resumption under strict risk invariants.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        bus: EventBus | None = None,
        state_repo: Any | None = None,
        production_repo: Any | None = None,
        risk_service: Any | None = None,
        trading_service: Any | None = None,
        event_log_repo: Any | None = None,
        notification_service: Any | None = None,
    ) -> None:
        self._config = config or get_config()
        self._bus = bus or EventBus()
        self._state_repo = state_repo
        self._production_repo = production_repo
        self._risk_service = risk_service
        self._trading_service = trading_service
        self._event_log_repo = event_log_repo
        self._notification_service = notification_service
        self._kill_switch_tripped = False

    async def initialize_state(self) -> None:
        """Initialize controller state from repository if available."""
        if self._production_repo and hasattr(
            self._production_repo, "get_runtime_state"
        ):
            state = await self._production_repo.get_runtime_state()
            mode = state.get("deployment_mode", "PAPER")
            if mode == "SHADOW":
                mode = "PAPER"
            self._config.deployment_mode = mode
            self._kill_switch_tripped = bool(state.get("global_kill_switch", False))
        elif self._state_repo and hasattr(self._state_repo, "get_runtime_state"):
            state = await self._state_repo.get_runtime_state()
            mode = state.get("deployment_mode", "PAPER")
            if mode == "SHADOW":
                mode = "PAPER"
            self._config.deployment_mode = mode
            self._kill_switch_tripped = bool(state.get("global_kill_switch", False))

    @property
    def is_kill_switch_tripped(self) -> bool:
        if self._kill_switch_tripped:
            return True
        if self._risk_service and hasattr(self._risk_service, "circuit_breaker"):
            return bool(self._risk_service.circuit_breaker.is_open)
        return False

    async def trip_kill_switch(
        self, reason: str = "Manual kill switch trip"
    ) -> dict[str, Any]:
        self._kill_switch_tripped = True
        if self._production_repo and hasattr(self._production_repo, "set_kill_switch"):
            await self._production_repo.set_kill_switch(True)
        return await self.kill_switch(reason=reason)

    async def reset_kill_switch(self) -> dict[str, Any]:
        self._kill_switch_tripped = False
        if self._production_repo and hasattr(self._production_repo, "set_kill_switch"):
            await self._production_repo.set_kill_switch(False)
        return await self.resume()

    def validate_order_safety(
        self,
        bot_name: str,
        amount: float,
        current_wallet_exposure_inr: float = 0.0,
    ) -> tuple[bool, str]:
        """Validate order size against safety boundaries."""
        if self.is_kill_switch_tripped:
            return (
                False,
                "Order rejected: Global kill switch is active — all orders halted.",
            )
        if amount < MINIMUM_NOTIONAL_INR:
            return (
                False,
                f"Order amount ₹{amount:.2f} is below minimum notional ₹{MINIMUM_NOTIONAL_INR:.2f}.",
            )
        if amount > 500.0:
            return False, f"Order amount ₹{amount:.2f} exceeds micro-order cap ₹500.00."
        if current_wallet_exposure_inr + amount > 15000.0:
            return (
                False,
                f"Order amount ₹{amount:.2f} exceeds wallet ceiling ₹15000.00.",
            )
        return True, "OK"

    @property
    def mode(self) -> DeploymentMode:
        """Return the current DeploymentMode enum value."""
        raw = self.get_active_mode()
        try:
            return DeploymentMode(raw)
        except Exception:
            return DeploymentMode.PAPER

    async def set_deployment_mode(
        self, target_mode: Any, operator: str = "API"
    ) -> DeploymentMode:
        """Set deployment mode accepting either a string or DeploymentMode enum."""
        mode_val = (
            target_mode.value if isinstance(target_mode, Enum) else str(target_mode)
        )
        await self.set_mode(mode_val, operator=operator)
        return self.mode

    def wire_dependencies(
        self,
        state_repo: Any | None = None,
        risk_service: Any | None = None,
        trading_service: Any | None = None,
        event_log_repo: Any | None = None,
        notification_service: Any | None = None,
    ) -> None:
        """Wire or update references after initial container bootstrap."""
        if state_repo is not None:
            self._state_repo = state_repo
        if risk_service is not None:
            self._risk_service = risk_service
        if trading_service is not None:
            self._trading_service = trading_service
        if event_log_repo is not None:
            self._event_log_repo = event_log_repo
        if notification_service is not None:
            self._notification_service = notification_service

    def get_active_mode(self) -> str:
        """Return the currently configured deployment mode."""
        return getattr(self._config, "deployment_mode", "PAPER")

    async def set_mode(self, target_mode: str, operator: str = "API") -> dict[str, Any]:
        """
        Dynamically transition execution mode (PAPER, LIVE / LIVE_MICROCASH).
        Persists changes to database and config override file.
        """
        raw_mode = target_mode.strip().upper()
        if raw_mode in ("LIVE", "LIVE_MICROCASH"):
            mode = "LIVE"
        elif raw_mode == "PAPER":
            mode = "PAPER"
        else:
            raise ValueError(
                f"Invalid mode '{target_mode}'. Valid modes: PAPER, LIVE"
            )

        if mode == "LIVE":
            if (
                self._risk_service
                and hasattr(self._risk_service, "circuit_breaker")
                and self._risk_service.circuit_breaker.is_open
            ):
                raise ValueError(
                    f"Cannot transition to LIVE: Circuit breaker is OPEN ({self._risk_service.circuit_breaker.reason})"
                )

            # P0-05 Security: Validate credentials before setting LIVE mode
            self._config.validate_live_security()

            if self._trading_service and hasattr(
                self._trading_service, "subaccount_manager"
            ):
                sub_mgr = self._trading_service.subaccount_manager
                if (
                    self._config.coindcx_api_key
                    and "mock" not in str(self._config.coindcx_api_key).lower()
                    and "test" not in str(self._config.coindcx_api_key).lower()
                    and "PYTEST_CURRENT_TEST" not in os.environ
                ):
                    bal_res = await sub_mgr.check_account_connectivity()
                    if not bal_res.get("success"):
                        raise ValueError(
                            f"Cannot transition to LIVE: CoinDCX connectivity check failed ({bal_res.get('error') or bal_res.get('message')})"
                        )
            self._config.deployment_mode = "LIVE"
            self._config.trading_enabled = True
            self._config.shadow_mode = False
            msg = f"Mode transitioned to LIVE. Real micro-orders (₹{self._config.order_size_inr:.2f}) dispatch to CoinDCX."
        else:
            self._config.deployment_mode = "PAPER"
            self._config.trading_enabled = True
            self._config.shadow_mode = False
            msg = "Mode transitioned to PAPER. Virtual positions execute with live prices, SL/TP exits, and 1.572% friction."

        # 1. Persist config overrides to filesystem
        try:
            AppConfig.save_runtime_overrides(
                {
                    "deployment_mode": self._config.deployment_mode,
                    "trading_enabled": self._config.trading_enabled,
                    "shadow_mode": self._config.shadow_mode,
                }
            )
        except Exception as exc:
            logger.warning("Could not persist runtime override for set_mode: %s", exc)

        # 2. Persist to production_runtime_state table in SQLite
        if self._production_repo and hasattr(
            self._production_repo, "set_deployment_mode"
        ):
            try:
                await self._production_repo.set_deployment_mode(mode)
            except Exception as exc:
                logger.warning("Failed to persist mode to production_repo: %s", exc)

        if self._state_repo:
            try:
                await self._state_repo.set_many(
                    {
                        "v2_deployment_mode": self._config.v2_deployment_mode,
                        "v2_trading_enabled": str(
                            self._config.v2_trading_enabled
                        ).lower(),
                        "v2_shadow_mode": str(self._config.v2_shadow_mode).lower(),
                        "deployment_mode": self._config.deployment_mode,
                        "trading_enabled": str(self._config.trading_enabled).lower(),
                        "shadow_mode": str(self._config.shadow_mode).lower(),
                        "mode_updated_at": datetime.now(timezone.utc).isoformat(),
                        "mode_updated_by": operator,
                    },
                    updated_by=operator,
                )
            except Exception as exc:
                logger.warning("Failed to persist mode to SQLite state_repo: %s", exc)

        # 3. Log event
        if self._event_log_repo:
            try:
                await self._event_log_repo.append(
                    event_type="PRODUCTION_MODE_CHANGED",
                    payload={
                        "new_mode": mode,
                        "operator": operator,
                        "trading_enabled": self._config.trading_enabled,
                    },
                    source_service="production_controller",
                )
            except Exception as exc:
                logger.debug("Failed to log mode change event: %s", exc)

        # 4. Notify EventBus
        try:
            await self._bus.publish(
                EventType.SYSTEM_CONFIG_UPDATED,
                {
                    "deployment_mode": self._config.deployment_mode,
                    "trading_enabled": self._config.trading_enabled,
                    "shadow_mode": self._config.shadow_mode,
                    "v2_deployment_mode": self._config.deployment_mode,
                    "v2_trading_enabled": self._config.trading_enabled,
                    "v2_shadow_mode": self._config.shadow_mode,
                    "operator": operator,
                },
            )
        except Exception as exc:
            logger.debug("Bus notification failed: %s", exc)

        # 5. Broadcast to Telegram
        if self._notification_service and hasattr(
            self._notification_service, "telegram_client"
        ):
            try:
                await self._notification_service.telegram_client.send_message(
                    f"⚙️ <b>DEPLOYMENT MODE CHANGED</b>\n\n"
                    f"• <b>New Mode:</b> <code>{mode}</code>\n"
                    f"• <b>Operator:</b> <code>{operator}</code>\n"
                    f"• <b>Trading:</b> {'ENABLED' if self._config.trading_enabled else 'HALTED'}\n"
                    f"• <b>Info:</b> {msg}"
                )
            except Exception as exc:
                logger.debug("Failed sending mode change Telegram alert: %s", exc)

        logger.info("Production mode changed to %s by %s", mode, operator)

        return {
            "ok": True,
            "mode": mode,
            "deployment_mode": mode,
            "trading_enabled": self._config.trading_enabled,
            "shadow_mode": self._config.shadow_mode,
            "message": msg,
        }

    async def kill_switch(
        self,
        reason: str = "Emergency Kill-Switch Triggered",
        operator: str = "API",
    ) -> dict[str, Any]:
        """
        Emergency Halt: Immediately trips the global circuit breaker, sets trading_enabled=False,
        and halts all outbound orders.
        """
        logger.critical("EMERGENCY KILL-SWITCH TRIGGERED by %s: %s", operator, reason)

        # 1. Trip circuit breaker in RiskService
        if self._risk_service and hasattr(self._risk_service, "circuit_breaker"):
            self._risk_service.circuit_breaker.trip(reason)
            self._risk_service.circuit_breaker.set_emergency_stop(True, reason)

        # 2. Force configuration to fail-safe mode with trading halted
        self._config.trading_enabled = False
        self._config.deployment_mode = "PAPER"
        self._config.shadow_mode = False

        try:
            AppConfig.save_runtime_overrides(
                {
                    "deployment_mode": "PAPER",
                    "trading_enabled": False,
                    "shadow_mode": False,
                }
            )
        except Exception as exc:
            logger.warning(
                "Could not persist runtime overrides for kill-switch: %s", exc
            )

        now_str = datetime.now(timezone.utc).isoformat()

        # 3. Persist state to SQLite
        if self._state_repo:
            try:
                await self._state_repo.set_many(
                    {
                        "circuit_breaker_status": "TRIPPED",
                        "circuit_breaker_reason": reason,
                        "emergency_stop": "true",
                        "deployment_mode": "PAPER",
                        "trading_enabled": "false",
                        "shadow_mode": "false",
                        "last_kill_switch_at": now_str,
                        "kill_switch_operator": operator,
                    },
                    updated_by=operator,
                )
            except Exception as exc:
                logger.warning("Failed to persist kill-switch state to DB: %s", exc)

        # 4. Audit Log
        if self._event_log_repo:
            try:
                await self._event_log_repo.append(
                    event_type="CIRCUIT_BREAKER_TRIPPED",
                    payload={
                        "reason": reason,
                        "operator": operator,
                        "action": "KILL_SWITCH",
                    },
                    source_service="production_controller",
                )
            except Exception as exc:
                logger.debug("Failed to log kill switch event: %s", exc)

        # 5. Broadcast alert on EventBus
        try:
            await self._bus.publish(
                EventType.CIRCUIT_BREAKER_TRIPPED,
                {
                    "reason": reason,
                    "operator": operator,
                    "timestamp": now_str,
                },
            )
        except Exception as exc:
            logger.debug("Bus alert dispatch error: %s", exc)
        return {
            "ok": True,
            "circuit_breaker": "TRIPPED",
            "circuit_breaker_tripped": True,
            "mode": self._config.deployment_mode,
            "trading_enabled": False,
            "status": "KILL_SWITCH_TRIPPED",
            "message": f"Global circuit breaker tripped: {reason}. All live order dispatch blocked immediately.",
        }

    async def resume(
        self,
        operator: str = "API",
        target_mode: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Verify database integrity, reset circuit breaker, re-arm order router,
        and transition back to target_mode (defaults to PAPER).
        """
        logger.info(
            "Resuming trading operations requested by %s (target_mode: %s)",
            operator,
            target_mode,
        )

        # 1. Database Integrity Verification
        if self._state_repo:
            healthy = await self._state_repo.verify_integrity()
            if not healthy:
                err_msg = "Database integrity check FAILED (PRAGMA integrity_check). Refusing to resume."
                logger.critical(err_msg)
                return {
                    "ok": False,
                    "error": "INTEGRITY_CHECK_FAILED",
                    "message": err_msg,
                }

        # 2. Risk Engine Safety Verification
        if self._risk_service and hasattr(self._risk_service, "is_safe_to_resume"):
            check_fn = self._risk_service.is_safe_to_resume
            res = check_fn()
            if hasattr(res, "__await__") or asyncio.iscoroutine(res):
                is_safe, unsafe_reason = await res
            elif isinstance(res, tuple):
                is_safe, unsafe_reason = res
            else:
                is_safe, unsafe_reason = True, "Mock/Default"

            if not is_safe:
                logger.critical(
                    "Refusing to resume trading: Risk Engine reports unsafe state: %s",
                    unsafe_reason,
                )
                return {
                    "ok": False,
                    "error": "RISK_PRECONDITION_FAILED",
                    "message": f"Cannot resume trading: Risk Engine reports unsafe state ({unsafe_reason})",
                    "status": "HALTED",
                }

        # 3. Reset circuit breaker in RiskService (only after safety confirmation)
        if self._risk_service and hasattr(self._risk_service, "circuit_breaker"):
            self._risk_service.circuit_breaker.reset()
            self._risk_service.circuit_breaker.set_emergency_stop(False)

        # 4. Determine resume mode
        resume_mode = (target_mode or "PAPER").upper().strip()
        if resume_mode not in ("PAPER", "LIVE_MICROCASH", "LIVE"):
            resume_mode = "PAPER"

        # Apply mode
        await self.set_mode(resume_mode, operator=operator)

        now_str = datetime.now(timezone.utc).isoformat()

        # 4. Persist normalized state in SQLite
        if self._state_repo:
            try:
                await self._state_repo.set_many(
                    {
                        "circuit_breaker_status": "NORMAL",
                        "circuit_breaker_reason": "",
                        "emergency_stop": "false",
                        "last_resumed_at": now_str,
                        "resumed_operator": operator,
                    },
                    updated_by=operator,
                )
            except Exception as exc:
                logger.warning("Failed to persist resume state to DB: %s", exc)

        # 5. Audit Log
        if self._event_log_repo:
            try:
                await self._event_log_repo.append(
                    event_type="SYSTEM_RESUMED",
                    payload={"resumed_mode": resume_mode, "operator": operator},
                    source_service="production_controller",
                )
            except Exception as exc:
                logger.debug("Failed to log resume event: %s", exc)

        # 6. Broadcast resume event on EventBus
        try:
            await self._bus.publish(
                EventType.SYSTEM_CONFIG_UPDATED,
                {
                    "action": "RESUME",
                    "circuit_breaker": "NORMAL",
                    "mode": resume_mode,
                    "operator": operator,
                    "timestamp": now_str,
                },
            )
        except Exception as exc:
            logger.debug("Bus resume event dispatch error: %s", exc)

        return {
            "ok": True,
            "circuit_breaker": "NORMAL",
            "circuit_breaker_tripped": False,
            "mode": resume_mode,
            "trading_enabled": self._config.trading_enabled,
            "database_integrity": True,
            "message": f"Trading successfully resumed in {resume_mode} mode. Circuit breaker re-armed.",
        }
