"""
PROJECT-ALPHA Risk Evaluation & Capital Guard Service (Production Fleet Edition).

Enforces hard boundaries, portfolio allocation limits, position caps, and circuit breakers
for the 4 production bots (STE, HDA, VCP, BBS) before any live or shadow order execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotName,
    RiskDecision,
    RiskState,
)

from .capital_guard import CapitalGuard
from .circuit_breaker import CircuitBreaker

logger = get_logger("execution.risk")


class RiskService:
    """Central risk evaluation service for multi-bot capital allocation and circuit breaking."""

    def __init__(
        self,
        bus: EventBus,
        position_repo: PositionRepository,
        trade_repo: TradeRepository,
        event_log_repo: EventLogRepository,
        config: AppConfig,
    ) -> None:
        self._bus = bus
        self._position_repo = position_repo
        self._trade_repo = trade_repo
        self._event_log = event_log_repo
        self._config = config

        self._capital_guard = CapitalGuard(config)
        self._circuit_breaker = CircuitBreaker(config)
        self._cooldowns: dict[str, dict] = {}
        self._started = False

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def capital_guard(self) -> CapitalGuard:
        return self._capital_guard

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._bus.subscribe(EventType.SIGNAL_AI_CONFIRMED, self.on_signal_ai_confirmed)
        self._bus.subscribe(EventType.POSITION_CLOSED, self.on_position_closed)
        await self._bus.publish(EventType.SYSTEM_STARTUP, {"service": "risk_service"})
        logger.info("RiskService started")

    async def stop(self) -> None:
        self._started = False
        self._bus.unsubscribe(
            EventType.SIGNAL_AI_CONFIRMED, self.on_signal_ai_confirmed
        )
        self._bus.unsubscribe(EventType.POSITION_CLOSED, self.on_position_closed)
        logger.info("RiskService stopped")

    def set_trading_service(self, trading_service: Any) -> None:
        self._trading_service = trading_service

    # ── Trade Permission Evaluation ───────────────────────────────────────────

    async def check_trade_allowed(
        self,
        bot: BotName,
        requested_amount: float,
        coin: str | None = None,
        pair: str | None = None,
        available_capital: float | None = None,
    ) -> RiskDecision:
        """Run complete risk evaluation (CircuitBreaker + CapitalGuard + Live Balance) against live state."""
        # 1. Circuit breaker check
        breaker_dec = self._circuit_breaker.check_breaker(bot, requested_amount)
        if not breaker_dec.allowed:
            return breaker_dec

        # 2. Dynamic Live Balance Verification (In LIVE_MICROCASH mode)
        deployment_mode = getattr(self._config, "deployment_mode", "PAPER").upper()
        if deployment_mode in ("LIVE", "LIVE_MICROCASH"):
            live_cap = available_capital
            if (
                live_cap is None
                and hasattr(self, "_trading_service")
                and self._trading_service
            ):
                sub_mgr = getattr(self._trading_service, "subaccount_manager", None)
                if sub_mgr:
                    bal_res = await sub_mgr.check_account_connectivity()
                    if bal_res.get("success"):
                        live_cap = float(bal_res.get("inr_balance", 0.0))
            if live_cap is None:
                return RiskDecision(
                    allowed=False,
                    code="BLOCKED_BALANCE_UNAVAILABLE",
                    reason="CoinDCX live balance could not be obtained or verified (capital unknown). Failing closed.",
                    bot=bot,
                    amount=requested_amount,
                    adjusted_amount=0.0,
                    check_ms=0.0,
                )
            if requested_amount > live_cap:
                return RiskDecision(
                    allowed=False,
                    code="BLOCKED_INSUFFICIENT_CAPITAL",
                    reason=f"Requested amount ₹{requested_amount:.2f} exceeds CoinDCX live available balance ₹{live_cap:.2f}.",
                    bot=bot,
                    amount=requested_amount,
                    adjusted_amount=0.0,
                    check_ms=0.0,
                )

        # 3. Query live open positions for deployed capital
        open_positions = await self._position_repo.get_open_by_bot(bot)
        all_open = await self._position_repo.get_open()

        bot_deployed = sum(p.deployed_capital for p in open_positions)
        total_deployed = sum(p.deployed_capital for p in all_open)
        bot_pos_count = len(open_positions)

        # 4. CapitalGuard evaluation with single-coin lock, fleet capacity, and post-exit cooldown checks
        return self._capital_guard.check_trade(
            bot=bot,
            requested_amount=requested_amount,
            current_bot_deployed=bot_deployed,
            total_deployed=total_deployed,
            current_bot_positions=bot_pos_count,
            active_positions=all_open,
            current_coin=coin or pair,
            cooldowns=self._cooldowns,
        )

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def on_signal_ai_confirmed(
        self, event_type: EventType, payload: dict
    ) -> None:
        """Evaluate trade feasibility for an AI-confirmed candidate signal."""
        try:
            signal_id = payload.get("signal_id")
            coin = payload.get("coin", "UNKNOWN")
            pair = payload.get("pair") or f"{coin}/INR"
            ai_adjustments = payload.get("suggested_adjustments") or {}

            # Select target bot strategy archetype
            bot = self._select_bot_for_signal(payload)
            # Dynamic amount flow: check explicit amount or use dynamically configured order_size_inr (minimum ₹200.00)
            configured_default = self._get_default_amount_for_bot(bot)
            raw_req = float(
                payload.get("amount")
                or payload.get("order_size_inr")
                or configured_default
            )
            requested_base = max(200.0, raw_req)

            # Apply AI position size scaling multiplier
            size_multiplier = float(ai_adjustments.get("size_multiplier", 1.0))
            scaled_amount = requested_base * size_multiplier
            scaled_amount = max(200.0, scaled_amount)

            decision = await self.check_trade_allowed(
                bot, scaled_amount, coin=coin, pair=pair
            )

            if decision.allowed:
                raw_price = float(payload.get("price") or 0.0)
                if raw_price <= 0.0:
                    logger.warning(
                        "Risk check aborted: missing or invalid price (%.4f) in payload for %s %s",
                        raw_price,
                        coin,
                        signal_id,
                    )
                    return

                event_mode = (
                    "LIVE"
                    if str(getattr(self._config, "deployment_mode", "PAPER")).upper()
                    in ("LIVE", "LIVE_MICROCASH")
                    else "PAPER"
                )
                approved_payload = {
                    "signal_id": signal_id,
                    "coin": coin,
                    "pair": pair,
                    "bot": bot.value,
                    "price": raw_price,
                    "approved_amount": decision.adjusted_amount,
                    "ai_adjustments": ai_adjustments,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "mode": event_mode,
                    "service": f"ALPHA_{event_mode}",
                }
                await self._bus.publish(EventType.TRADE_APPROVED, approved_payload)
                await self._event_log.append(
                    event_type=EventType.TRADE_APPROVED.value,
                    source_service="risk_service",
                    entity_id=signal_id or "",
                    payload=approved_payload,
                )
                logger.info(
                    "Trade APPROVED by RiskService",
                    extra={
                        "coin": coin,
                        "bot": bot.value,
                        "amount": decision.adjusted_amount,
                    },
                )
            else:
                event_mode = (
                    "LIVE"
                    if str(getattr(self._config, "deployment_mode", "PAPER")).upper()
                    in ("LIVE", "LIVE_MICROCASH")
                    else "PAPER"
                )
                denied_payload = {
                    "signal_id": signal_id,
                    "coin": coin,
                    "pair": pair,
                    "bot": bot.value,
                    "requested_amount": scaled_amount,
                    "code": decision.code,
                    "reason": decision.reason,
                    "denied_at": datetime.now(timezone.utc).isoformat(),
                    "mode": event_mode,
                    "service": f"ALPHA_{event_mode}",
                }
                await self._bus.publish(EventType.TRADE_DENIED, denied_payload)
                await self._event_log.append(
                    event_type=EventType.TRADE_DENIED.value,
                    source_service="risk_service",
                    entity_id=signal_id or "",
                    payload=denied_payload,
                )
                logger.warning(
                    "Trade DENIED by RiskService",
                    extra={"coin": coin, "bot": bot.value, "reason": decision.reason},
                )

        except Exception:
            logger.error("Error evaluating trade in RiskService", exc_info=True)

    async def on_position_closed(self, event_type: EventType, payload: dict) -> None:
        """Update circuit breaker metrics and record post-exit cooldown on position exit."""
        try:
            bot_str = payload.get("bot", "")
            pnl = float(payload.get("pnl", 0.0))
            coin = (
                (payload.get("coin") or "")
                .upper()
                .replace("/INR", "")
                .replace("/USDT", "")
                .replace("B-", "")
            )
            if coin:
                self._cooldowns[coin] = {
                    "exit_time": datetime.now(timezone.utc),
                    "exit_reason": payload.get("exit_reason", "CLOSED"),
                }
            try:
                bot = BotName(bot_str)
                self._circuit_breaker.record_trade_result(bot, pnl)
            except ValueError:
                pass
        except Exception as exc:
            logger.warning(
                "Error processing position close in RiskService",
                extra={"error": str(exc)},
            )

    # ── Helpers & State Queries ───────────────────────────────────────────────

    def _select_bot_for_signal(self, payload: dict) -> BotName:
        bot_raw = payload.get("bot")
        if bot_raw:
            try:
                return BotName(bot_raw.upper())
            except ValueError:
                pass

        opp_type = str(
            payload.get("opportunity_type") or payload.get("market_state") or ""
        ).lower()
        if any(w in opp_type for w in ("volume", "absorption", "cvd", "delivery")):
            return BotName.HDA
        if any(w in opp_type for w in ("contraction", "vcp", "minervini")):
            return BotName.VCP
        if any(w in opp_type for w in ("squeeze", "bollinger", "keltner", "bbs")):
            return BotName.BBS
        return BotName.STE

    def _get_default_amount_for_bot(self, bot: BotName) -> float:
        if bot == BotName.STE and self._config.default_trade_amount_ste > 200.0:
            return float(self._config.default_trade_amount_ste)
        if bot == BotName.HDA and self._config.default_trade_amount_hda > 200.0:
            return float(self._config.default_trade_amount_hda)
        if bot == BotName.VCP and self._config.default_trade_amount_vcp > 200.0:
            return float(self._config.default_trade_amount_vcp)
        if bot == BotName.BBS and self._config.default_trade_amount_bbs > 200.0:
            return float(self._config.default_trade_amount_bbs)
        return max(200.0, float(self._config.order_size_inr))

    async def is_safe_to_resume(self) -> tuple[bool, str]:
        """
        Verify if it is safe to resume trading operations.
        MUST NOT bypass:
          - Circuit breaker trip state due to risk breaches (e.g. consecutive losses)
          - Daily / weekly loss limits & max daily drawdown
          - Capital protection (verifies balance in LIVE mode)
          - Exchange safety checks
        Resume may restore the production controller after a kill-switch,
        but it must not force trading when Risk Engine says trading is unsafe.
        """
        # 1. Check consecutive loss limits
        for bot_name, losses in self._circuit_breaker._consecutive_losses.items():
            if losses >= self._config.max_consecutive_losses:
                return (
                    False,
                    f"Strategy {bot_name} exceeded max consecutive losses ({losses}/{self._config.max_consecutive_losses})",
                )

        # 2. Check if circuit breaker was tripped by a risk breach / loss event
        cb_reason = (self._circuit_breaker.reason or "").lower()
        if self._circuit_breaker.is_open:
            if any(
                w in cb_reason
                for w in ("drawdown", "consecutive", "loss", "breach", "threshold")
            ):
                return (
                    False,
                    f"Risk Engine threshold breach active: {self._circuit_breaker.reason}",
                )
            if not self._circuit_breaker.emergency_stop:
                return (
                    False,
                    f"Circuit breaker is open: {self._circuit_breaker.reason or 'Risk limit exceeded'}",
                )

        # 3. Dynamic capital & exchange safety in LIVE mode
        deployment_mode = getattr(self._config, "deployment_mode", "PAPER").upper()
        if deployment_mode in ("LIVE", "LIVE_MICROCASH"):
            if not self._config.coindcx_api_key or not self._config.coindcx_api_secret:
                return False, "CoinDCX API credentials missing or incomplete"
            if hasattr(self, "_trading_service") and self._trading_service:
                sub_mgr = getattr(self._trading_service, "subaccount_manager", None)
                if sub_mgr:
                    bal_res = await sub_mgr.check_account_connectivity()
                    if not bal_res.get("success"):
                        return (
                            False,
                            f"CoinDCX connectivity check failed: {bal_res.get('error') or bal_res.get('message')}",
                        )
                    inr_bal = float(bal_res.get("inr_balance", 0.0))
                    if inr_bal <= 0.0:
                        return (
                            False,
                            f"CoinDCX available INR balance is insufficient (₹{inr_bal:.2f})",
                        )

        return True, "Safe to resume operations"

    async def get_state(self) -> RiskState:
        open_by_bot = {
            bot: await self._position_repo.get_open_by_bot(bot) for bot in BotName
        }

        return RiskState(
            trading_enabled=self._config.trading_enabled,
            emergency_stop=self._circuit_breaker.emergency_stop,
            circuit_breaker_open=self._circuit_breaker.is_open,
            per_bot_deployed={
                bot.value: sum(p.deployed_capital for p in positions)
                for bot, positions in open_by_bot.items()
            },
            per_bot_open_count={
                bot.value: len(positions) for bot, positions in open_by_bot.items()
            },
            daily_pnl={},
            last_checked_at=datetime.now(timezone.utc),
        )

    def get_health(self) -> dict:
        return {
            "healthy": self._started,
            "trading_enabled": self._config.trading_enabled,
            "circuit_breaker_open": self._circuit_breaker.is_open,
            "emergency_stop": self._circuit_breaker.emergency_stop,
            "breaker_reason": self._circuit_breaker.reason,
            "total_capital_limit": self._config.total_capital_limit,
            "ste_capital_limit": self._config.ste_capital_limit,
            "hda_capital_limit": self._config.hda_capital_limit,
            "vcp_capital_limit": self._config.vcp_capital_limit,
            "bbs_capital_limit": self._config.bbs_capital_limit,
            "native_oco_supported": False,
            "protective_orders_source": "ENGINE_POLLING",
        }
