"""
V2 NotificationService — central event listener and alert coordinator.
"""

from __future__ import annotations

from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger

from .formatters import (
    format_circuit_breaker_alert,
    format_divergence_alert,
    format_generic_alert,
    format_position_closed_alert,
    format_position_opened_alert,
    format_signal_ai_alert,
    format_trade_approved_alert,
    format_trade_denied_alert,
)
from .telegram import TelegramClient
from .telegram_interface import TelegramInteractiveInterface
from .routing import notification_mode

logger = get_logger("telegram.service")


class NotificationService:
    """Dispatches trade alerts and supervises interactive Telegram C2 interfaces for Paper & Live."""

    def __init__(
        self,
        bus: EventBus,
        config: AppConfig,
        telegram_client: TelegramClient | None = None,
        signal_repo: Any | None = None,
        position_repo: Any | None = None,
        trade_repo: Any | None = None,
        portfolio_service: Any | None = None,
        risk_service: Any | None = None,
        trading_service: Any | None = None,
        scanner_service: Any | None = None,
        health_checker: Any | None = None,
        event_log_repo: Any | None = None,
        production_controller: Any | None = None,
    ) -> None:
        self._bus = bus
        self._config = config

        # Resolve Live Trading Telegram Client. Legacy ALERT_* aliases are LIVE-only.
        live_token = getattr(config, "live_bot_token", None) or getattr(config, "alert_bot_token", None)
        live_chat = getattr(config, "live_chat_id", None) or getattr(config, "alert_chat_id", None)
        self._live_telegram = telegram_client or TelegramClient(
            bot_token=live_token,
            chat_id=live_chat,
        )
        self._telegram = self._live_telegram  # Backward compatibility reference

        # Paper has no fallback to LIVE credentials and never reuses an injected
        # LIVE client. Ambiguous credentials fail closed.
        paper_token = getattr(config, "paper_bot_token", None)
        paper_chat = getattr(config, "paper_chat_id", None)
        if paper_token and live_token and paper_token == live_token:
            logger.error("Paper Telegram disabled: PAPER and LIVE bot tokens must differ")
            paper_token = None
            paper_chat = None
        self._paper_telegram = TelegramClient(
            bot_token=paper_token,
            chat_id=paper_chat,
            resolve_default_credentials=False,
        )

        # Primary Live Interactive Interface
        self._interactive_interface = TelegramInteractiveInterface(
            telegram_client=self._live_telegram,
            bus=self._bus,
            config=self._config,
            signal_repo=signal_repo,
            position_repo=position_repo,
            trade_repo=trade_repo,
            portfolio_service=portfolio_service,
            risk_service=risk_service,
            trading_service=trading_service,
            scanner_service=scanner_service,
            health_checker=health_checker,
            event_log_repo=event_log_repo,
            production_controller=production_controller,
            mode_override="LIVE",
        )

        # Dedicated Paper Interactive Interface (if separate bot token provided)
        self._paper_interface: TelegramInteractiveInterface | None = None
        if paper_token and paper_token != live_token:
            self._paper_interface = TelegramInteractiveInterface(
                telegram_client=self._paper_telegram,
                bus=self._bus,
                config=self._config,
                signal_repo=signal_repo,
                position_repo=position_repo,
                trade_repo=trade_repo,
                portfolio_service=portfolio_service,
                risk_service=risk_service,
                trading_service=trading_service,
                scanner_service=scanner_service,
                health_checker=health_checker,
                event_log_repo=event_log_repo,
                production_controller=production_controller,
                mode_override="PAPER",
            )

        self._total_dispatched = 0
        self._alert_rate_limits: dict[str, float] = {}
        self._dedup_cache: dict[tuple[str, str, str], float] = {}
        self._started = False

    @property
    def telegram_client(self) -> TelegramClient:
        return self._live_telegram

    @property
    def paper_telegram_client(self) -> TelegramClient:
        return self._paper_telegram

    @property
    def live_telegram_client(self) -> TelegramClient:
        return self._live_telegram

    @property
    def interactive_interface(self) -> TelegramInteractiveInterface:
        return self._interactive_interface

    @property
    def paper_interactive_interface(self) -> TelegramInteractiveInterface | None:
        return self._paper_interface

    def _is_duplicate_alert(
        self, event_type: str, coin: str, reason: str, ttl_seconds: float = 1800.0
    ) -> bool:
        """In-memory alert dedup counter keyed on (event_type, coin, reason) with a flush window."""
        import time as _time

        now_ts = _time.time()
        key = (str(event_type).upper(), str(coin).upper(), str(reason).strip())
        last_sent = self._dedup_cache.get(key, 0.0)
        if now_ts - last_sent < ttl_seconds:
            return True
        self._dedup_cache[key] = now_ts
        # Prune old cache entries
        if len(self._dedup_cache) > 200:
            self._dedup_cache = {
                k: ts
                for k, ts in self._dedup_cache.items()
                if now_ts - ts < ttl_seconds
            }
        return False

    def wire_dependencies(
        self,
        signal_repo: Any | None = None,
        position_repo: Any | None = None,
        trade_repo: Any | None = None,
        portfolio_service: Any | None = None,
        risk_service: Any | None = None,
        trading_service: Any | None = None,
        scanner_service: Any | None = None,
        health_checker: Any | None = None,
        event_log_repo: Any | None = None,
        production_controller: Any | None = None,
    ) -> None:
        """Dynamically wire late-bound subsystem references into both interactive interfaces."""
        interfaces = [self._interactive_interface]
        if self._paper_interface:
            interfaces.append(self._paper_interface)

        for iface in interfaces:
            if signal_repo is not None:
                iface._signal_repo = signal_repo
            if position_repo is not None:
                iface._position_repo = position_repo
            if trade_repo is not None:
                iface._trade_repo = trade_repo
            if portfolio_service is not None:
                iface._portfolio_service = portfolio_service
            if risk_service is not None:
                iface._risk_service = risk_service
            if trading_service is not None:
                iface._trading_service = trading_service
            if scanner_service is not None:
                iface._scanner_service = scanner_service
            if health_checker is not None:
                iface._health_checker = health_checker
            if event_log_repo is not None:
                iface._event_log_repo = event_log_repo
            if production_controller is not None:
                iface._production_controller = production_controller

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(
        self,
        enable_polling: bool = True,
        enable_trade_alerts: bool = True,
    ) -> None:
        if self._started:
            return
        self._started = True
        self._enable_trade_alerts = enable_trade_alerts
        # Essential high-conviction events only (to prevent notification spam)
        if enable_trade_alerts:
            self._bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
            self._bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)
            self._bus.subscribe(EventType.TRADE_DENIED, self._on_trade_denied)
        self._bus.subscribe(
            EventType.CIRCUIT_BREAKER_TRIGGERED, self._on_circuit_breaker
        )
        self._bus.subscribe(EventType.ALERT_GENERATED, self._on_alert_generated)
        await self._bus.publish(
            EventType.SYSTEM_STARTUP, {"service": "notification_service"}
        )
        if enable_polling:
            await self._interactive_interface.start()
            if self._paper_interface:
                await self._paper_interface.start()
        logger.info(
            "NotificationService started",
            extra={
                "enable_polling": enable_polling,
                "enable_trade_alerts": enable_trade_alerts,
                "live_telegram_configured": self._live_telegram.is_configured,
                "paper_telegram_configured": self._paper_telegram.is_configured,
            },
        )

    async def stop(self) -> None:
        self._started = False
        await self._interactive_interface.stop()
        if self._paper_interface:
            await self._paper_interface.stop()
        if getattr(self, "_enable_trade_alerts", True):
            self._bus.unsubscribe(EventType.POSITION_OPENED, self._on_position_opened)
            self._bus.unsubscribe(EventType.POSITION_CLOSED, self._on_position_closed)
            self._bus.unsubscribe(EventType.TRADE_DENIED, self._on_trade_denied)
        self._bus.unsubscribe(
            EventType.CIRCUIT_BREAKER_TRIGGERED, self._on_circuit_breaker
        )
        self._bus.unsubscribe(EventType.ALERT_GENERATED, self._on_alert_generated)
        logger.info("NotificationService stopped")

    # ── Target Resolution Helper ──────────────────────────────────────────────

    def _resolve_target_telegram(
        self, payload: dict[str, Any] | None = None
    ) -> TelegramClient | None:
        """Resolve an explicitly identified notification; never infer a mode."""
        mode = notification_mode(payload)
        if mode == "PAPER":
            return self._paper_telegram
        if mode == "LIVE":
            return self._live_telegram
        return None

    # ── Dispatch Handlers ─────────────────────────────────────────────────────

    async def send_custom_alert(self, text: str, mode: str | None = None) -> bool:
        """Manually dispatch a custom alert through the appropriate pipeline."""
        target = self._resolve_target_telegram({"mode": mode, "service": f"{mode or ''}_CUSTOM"})
        if target is None:
            return False
        sent = await target.send_message(text)
        if sent:
            self._total_dispatched += 1
        return sent

    async def _on_signal_ai_confirmed(
        self, event_type: EventType, payload: dict
    ) -> None:
        try:
            coin = payload.get("coin", "UNKNOWN")
            rec = payload.get("recommendation", "WATCH")
            if self._is_duplicate_alert("SIGNAL_AI", coin, rec):
                return
            msg = format_signal_ai_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning("Error dispatching AI alert", extra={"error": str(exc)})

    async def _on_trade_approved(self, event_type: EventType, payload: dict) -> None:
        try:
            msg = format_trade_approved_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning(
                "Error dispatching Trade Approved alert", extra={"error": str(exc)}
            )

    async def _on_trade_denied(self, event_type: EventType, payload: dict) -> None:
        try:
            coin = payload.get("coin", "UNKNOWN")
            reason = payload.get("reason", "Capital limit reached")
            c2_score = int(
                payload.get("confluence_score")
                or payload.get("c2_score")
                or payload.get("score")
                or 0
            )
            ai_rec = (
                payload.get("ai_recommendation")
                or payload.get("recommendation")
                or "APPROVE"
            )

            # Risk rejection alerts only fire for high-conviction candidates (C2 >= 85, AI-approved)
            if c2_score < 85 or ai_rec not in ("APPROVE", "SCALE_DOWN"):
                logger.debug(
                    "Suppressing low-conviction trade denied alert for %s (C2: %d, AI: %s)",
                    coin,
                    c2_score,
                    ai_rec,
                )
                return

            if self._is_duplicate_alert("TRADE_DENIED", coin, reason):
                logger.debug(
                    "Suppressing duplicate Trade Denied alert for %s: %s", coin, reason
                )
                return

            msg = format_trade_denied_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning(
                "Error dispatching Trade Denied alert", extra={"error": str(exc)}
            )

    async def _on_position_opened(self, event_type: EventType, payload: dict) -> None:
        try:
            msg = format_position_opened_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning(
                "Error dispatching Position Opened alert", extra={"error": str(exc)}
            )

    async def _on_position_closed(self, event_type: EventType, payload: dict) -> None:
        try:
            msg = format_position_closed_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning(
                "Error dispatching Position Closed alert", extra={"error": str(exc)}
            )

    async def _on_circuit_breaker(self, event_type: EventType, payload: dict) -> None:
        try:
            reason = payload.get("reason", "Threshold breached")
            if self._is_duplicate_alert(
                "CIRCUIT_BREAKER", "SYSTEM", reason, ttl_seconds=600.0
            ):
                return
            msg = format_circuit_breaker_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning(
                "Error dispatching Circuit Breaker alert", extra={"error": str(exc)}
            )

    async def _on_divergence(self, event_type: EventType, payload: dict) -> None:
        try:
            coin = payload.get("coin", "UNKNOWN")
            div_type = payload.get("divergence_type", "AI_FILTERED")
            if self._is_duplicate_alert("DIVERGENCE", coin, div_type):
                return
            msg = format_divergence_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning(
                "Error dispatching Divergence alert", extra={"error": str(exc)}
            )

    async def _on_alert_generated(self, event_type: EventType, payload: dict) -> None:
        try:
            title = payload.get("title", "Generic Alert")
            coin = payload.get("coin", "SYSTEM")
            msg_body = payload.get("message", "")
            if self._is_duplicate_alert(
                "ALERT_GENERATED", coin, f"{title}:{msg_body[:40]}"
            ):
                logger.debug("Suppressing duplicate Telegram alert: %s", title)
                return

            msg = format_generic_alert(payload)
            target = self._resolve_target_telegram(payload)
            if target is not None and await target.send_message(msg):
                self._total_dispatched += 1
        except Exception as exc:
            logger.warning("Error dispatching Generic alert", extra={"error": str(exc)})

    def get_health(self) -> dict:
        return {
            "healthy": self._started,
            "live_telegram_configured": self._live_telegram.is_configured,
            "paper_telegram_configured": self._paper_telegram.is_configured,
            "total_dispatched": self._total_dispatched,
        }
