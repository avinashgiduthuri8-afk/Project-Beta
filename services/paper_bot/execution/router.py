"""
PROJECT-ALPHA — Paper Execution Router & Safeguards.

Handles signal validation, risk checks, paper order execution, and position state.
Strictly rejects any signal or order where mode != PAPER.
Never accesses LIVE trading state or Live DB.
"""

from datetime import datetime, timezone
import inspect
from typing import TYPE_CHECKING, Any
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.types import BotMode, BotName
from execution.position_manager import PositionManager
from execution.trading.precision_rules import extract_base_coin
from shared.mode_guard import ModeIsolationError, validate_execution_mode

logger = get_logger("paper.execution.router")

if TYPE_CHECKING:
    from execution.auto_trader import AutoTradeRouter


class PaperExecutionRouter:
    """
    Paper Execution Router.
    Validates paper signals, executes paper simulation, and persists paper positions.
    """

    def __init__(
        self,
        bus: EventBus,
        auto_trader: "AutoTradeRouter",
        position_manager: PositionManager | None = None,
        capital_guard: Any | None = None,
        position_repo: Any | None = None,
    ) -> None:
        self.bus = bus
        self.auto_trader = auto_trader
        self.position_manager = position_manager
        self.capital_guard = capital_guard
        self.position_repo = position_repo

    async def handle_signal_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        EventBus callback for SIGNAL_GENERATED.
        Enforces paper mode isolation and strict STE signal contract validation.
        """
        # 1. Hard Safeguard: Mode Isolation (Reject mode != PAPER)
        try:
            validate_execution_mode(payload, expected_mode="PAPER", component="PaperExecutionRouter")
        except ModeIsolationError as err:
            logger.warning("PaperExecutionRouter rejected signal: %s", err)
            return {
                "success": False,
                "rejected": True,
                "error": "MODE_ISOLATION_ERROR",
                "reason": str(err),
            }

        # 2. Required Fields Validation
        signal_id = str(payload.get("id") or payload.get("signal_id") or "")
        pair_val = payload.get("pair")
        coin_val = payload.get("coin") or (extract_base_coin(pair_val) if pair_val else None)
        raw_price = payload.get("price") if payload.get("price") is not None else payload.get("entry_price")

        if not signal_id or not pair_val or not coin_val or raw_price is None:
            missing = []
            if not signal_id: missing.append("signal_id")
            if not pair_val: missing.append("pair")
            if not coin_val: missing.append("coin")
            if raw_price is None: missing.append("price")
            logger.warning("PaperExecutionRouter rejected signal: missing required fields %s", missing)
            return {
                "success": False,
                "rejected": True,
                "error": "MISSING_REQUIRED_FIELDS",
                "reason": f"Signal missing required fields: {', '.join(missing)}",
            }

        # 3. Numeric Price Validation
        try:
            entry_px = float(raw_price)
            if entry_px <= 0.0 or not (entry_px == entry_px):
                return {
                    "success": False,
                    "rejected": True,
                    "error": "INVALID_ENTRY_PRICE",
                    "reason": f"Signal has invalid price: {raw_price}",
                }
        except (TypeError, ValueError) as err:
            return {
                "success": False,
                "rejected": True,
                "error": "INVALID_ENTRY_PRICE",
                "reason": f"Signal has non-numeric price: {err}",
            }

        # 4. Signal TTL / Expiry Validation
        now_utc = datetime.now(timezone.utc)
        expires_at_raw = payload.get("expires_at")
        if expires_at_raw:
            try:
                if isinstance(expires_at_raw, str):
                    exp_dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                elif isinstance(expires_at_raw, datetime):
                    exp_dt = expires_at_raw if expires_at_raw.tzinfo else expires_at_raw.replace(tzinfo=timezone.utc)
                else:
                    exp_dt = None
                if exp_dt and now_utc > exp_dt:
                    logger.warning("PaperExecutionRouter rejected stale signal %s: expired at %s", signal_id, exp_dt)
                    return {
                        "success": False,
                        "rejected": True,
                        "error": "SIGNAL_EXPIRED",
                        "reason": f"Signal {signal_id} has expired (expired_at: {exp_dt.isoformat()})",
                    }
            except Exception as exp_err:
                logger.debug("Error checking expires_at: %s", exp_err)

        # 5. Strategy Validation
        bot_val = payload.get("strategy") or payload.get("bot") or "STE"
        bot_str = str(bot_val).upper()
        valid_strategies = {
            "STE", "SUPERTREND ATR RANGE EXPANSION",
            "HDA", "HIGH DELIVERY ABSORPTION", "HIGH DELIVERY & CVD ABSORPTION",
            "VCP", "VOLATILITY CONTRACTION PATTERN",
            "BBS", "BOLLINGER BAND SQUEEZE", "BOLLINGER BAND SQUEEZE BREAKOUT", "BOLLINGER-KELTNER SQUEEZE",
            "MTB", "MOMENTUM TREND BREAKOUT", "MOMENTUM TREND",
            "NR7", "NARROW RANGE", "NARROW RANGE 7", "NARROW RANGE 7 SQUEEZE",
            "PPA", "POCKET PIVOT", "POCKET PIVOT ACCUMULATION",
            "MRB", "MEAN REVERSION", "MEAN REVERSION BOUNCE",
        }
        if not any(v in bot_str for v in valid_strategies):
            return {
                "success": False,
                "rejected": True,
                "error": "INVALID_STRATEGY",
                "reason": f"Unsupported strategy '{bot_val}'",
            }

        # 6. TP / SL Numerical Sanity Validation
        sl_val = payload.get("stop_loss")
        tp_val = payload.get("take_profit")
        if sl_val is not None:
            try:
                sl_float = float(sl_val)
                if sl_float <= 0.0 or sl_float >= entry_px:
                    return {
                        "success": False,
                        "rejected": True,
                        "error": "INVALID_TP_SL",
                        "reason": f"Stop loss {sl_float} must be positive and below entry price {entry_px}",
                    }
            except (ValueError, TypeError):
                return {
                    "success": False,
                    "rejected": True,
                    "error": "INVALID_TP_SL",
                    "reason": f"Invalid stop loss value: {sl_val}",
                }
        if tp_val is not None:
            try:
                tp_float = float(tp_val)
                if tp_float <= 0.0 or tp_float <= entry_px:
                    return {
                        "success": False,
                        "rejected": True,
                        "error": "INVALID_TP_SL",
                        "reason": f"Take profit {tp_float} must be positive and above entry price {entry_px}",
                    }
            except (ValueError, TypeError):
                return {
                    "success": False,
                    "rejected": True,
                    "error": "INVALID_TP_SL",
                    "reason": f"Invalid take profit value: {tp_val}",
                }

        # 7. Signal Idempotency & Persistent Replay Validation
        if hasattr(self.auto_trader, "is_signal_processed") and hasattr(self.auto_trader, "generate_idempotency_key"):
            idempotency_key = self.auto_trader.generate_idempotency_key(coin_val, signal_id)
            if self.auto_trader.is_signal_processed(idempotency_key):
                logger.warning("Duplicate signal rejected by idempotency filter: %s", idempotency_key)
                return {
                    "success": False,
                    "rejected": True,
                    "error": "DUPLICATE_SIGNAL",
                    "idempotency_key": idempotency_key,
                    "reason": f"Signal {idempotency_key} has already been processed.",
                }

        pos_repo = self.position_repo or getattr(self.auto_trader, "_position_repo", None)
        if pos_repo and hasattr(pos_repo, "get_by_signal_id") and signal_id:
            existing_pos = await pos_repo.get_by_signal_id(signal_id)
            if existing_pos:
                logger.warning(
                    "Signal %s already executed as position %s, rejecting duplicate replay",
                    signal_id,
                    existing_pos.id,
                )
                return {
                    "success": False,
                    "rejected": True,
                    "error": "SIGNAL_ALREADY_EXECUTED",
                    "position_id": existing_pos.id,
                    "reason": f"Signal {signal_id} has already been executed as position {existing_pos.id}.",
                }

        # 8. Capital / Budget Guard Validation
        if self.capital_guard:
            pos_repo = self.position_repo or getattr(self.auto_trader, "_position_repo", None)
            active_positions = []
            total_deployed = 0.0
            current_bot_positions = 0
            current_bot_deployed = 0.0
            target_bot_enum = BotName.STE
            for b in BotName:
                if b.value in bot_str:
                    target_bot_enum = b
                    break
            if target_bot_enum == BotName.STE:
                if "HDA" in bot_str or "ABSORPTION" in bot_str:
                    target_bot_enum = BotName.HDA
                elif "VCP" in bot_str or "CONTRACTION" in bot_str:
                    target_bot_enum = BotName.VCP
                elif "BBS" in bot_str or "SQUEEZE" in bot_str:
                    target_bot_enum = BotName.BBS
                elif "MTB" in bot_str or "MOMENTUM TREND" in bot_str:
                    target_bot_enum = BotName.MTB
                elif "NR7" in bot_str or "NARROW RANGE" in bot_str:
                    target_bot_enum = BotName.NR7
                elif "PPA" in bot_str or "POCKET PIVOT" in bot_str:
                    target_bot_enum = BotName.PPA
                elif "MRB" in bot_str or "MEAN REVERSION" in bot_str:
                    target_bot_enum = BotName.MRB

            if pos_repo and hasattr(pos_repo, "get_open"):
                get_open_coro = pos_repo.get_open()
                if inspect.isawaitable(get_open_coro):
                    active_positions = await get_open_coro
                else:
                    active_positions = get_open_coro
                for p in active_positions:
                    notional = float(getattr(p, "entry_price", 0.0)) * float(getattr(p, "qty", 0.0))
                    p_pair = str(getattr(p, "pair", ""))
                    if p_pair.endswith("USDT"):
                        notional *= float(payload.get("usdt_inr_rate", 91.50))
                    total_deployed += notional
                    p_bot = getattr(p, "bot", None)
                    if p_bot == target_bot_enum or getattr(p_bot, "value", "") == target_bot_enum.value:
                        current_bot_positions += 1
                        current_bot_deployed += notional

            requested_amount = float(payload.get("amount") or payload.get("trade_amount") or 200.0)
            decision = self.capital_guard.check_trade(
                bot=target_bot_enum,
                requested_amount=requested_amount,
                current_bot_deployed=current_bot_deployed,
                total_deployed=total_deployed,
                current_bot_positions=current_bot_positions,
                active_positions=active_positions,
                current_coin=coin_val,
            )
            if not decision.allowed:
                err_code = "INSUFFICIENT_CAPITAL" if decision.code in ("BLOCKED_TOTAL_CAPITAL", "BLOCKED_BOT_CAPITAL") else decision.code
                logger.warning("PaperExecutionRouter capital check failed: %s (%s)", decision.reason, err_code)
                return {
                    "success": False,
                    "rejected": True,
                    "error": err_code,
                    "decision_code": decision.code,
                    "reason": decision.reason,
                }

        # 8. Process signal in paper simulation mode (dry_run=True)
        logger.info("Paper execution router processing signal: %s", signal_id)
        result = await self.auto_trader.handle_signal(payload, dry_run=True)

        if not result or not result.get("success"):
            err_name = result.get("error", "EXECUTION_REJECTED") if result else "EXECUTION_REJECTED"
            msg = result.get("message") if result else "Signal execution rejected"
            logger.warning("AutoTrader rejected signal %s: %s", signal_id, err_name)
            return {
                "success": False,
                "rejected": True,
                "error": err_name,
                "reason": msg,
                **(result or {}),
            }

        # 9. Persist Position
        if not self.position_manager:
            logger.error("Paper execution router cannot persist position: position_manager is None")
            result["success"] = False
            result["error"] = "POSITION_MANAGER_UNAVAILABLE"
            result["message"] = "PositionManager is not configured on PaperExecutionRouter."
            return result

        try:
            bot_val = result.get("bot") or payload.get("bot") or payload.get("strategy") or "STE"
            bot_str_res = str(bot_val).upper()
            bot_enum = BotName.STE
            for b in BotName:
                if b.value == bot_str_res or b.value in bot_str_res:
                    bot_enum = b
                    break
            if bot_enum == BotName.STE:
                if "HDA" in bot_str_res or "ABSORPTION" in bot_str_res:
                    bot_enum = BotName.HDA
                elif "VCP" in bot_str_res or "CONTRACTION" in bot_str_res:
                    bot_enum = BotName.VCP
                elif "BBS" in bot_str_res or "SQUEEZE" in bot_str_res:
                    bot_enum = BotName.BBS
                elif "MTB" in bot_str_res or "MOMENTUM TREND" in bot_str_res:
                    bot_enum = BotName.MTB
                elif "NR7" in bot_str_res or "NARROW RANGE" in bot_str_res:
                    bot_enum = BotName.NR7
                elif "PPA" in bot_str_res or "POCKET PIVOT" in bot_str_res:
                    bot_enum = BotName.PPA
                elif "MRB" in bot_str_res or "MEAN REVERSION" in bot_str_res:
                    bot_enum = BotName.MRB

            pair_val = result.get("pair") or payload.get("pair") or "BTC/INR"
            coin_val = payload.get("coin") or extract_base_coin(pair_val) or "BTC"
            entry_px = float(result.get("price"))
            qty_val = float(result.get("qty"))

            # Dynamic Take Profit & Risk Management rules:
            score = float(payload.get("score") or 85.0)
            sl_val = payload.get("stop_loss")
            tp_val = payload.get("take_profit")

            if sl_val is None:
                if bot_enum == BotName.HDA:
                    sl_val = round(entry_px * (1.0 - 0.045), 8)  # 4.5% HDA SL default
                elif bot_enum == BotName.VCP:
                    sl_val = round(entry_px * (1.0 - 0.048), 8)  # 4.8% VCP SL default
                elif bot_enum == BotName.BBS:
                    sl_val = round(entry_px * (1.0 - 0.045), 8)  # 4.5% BBS SL default
                elif bot_enum == BotName.MTB:
                    sl_val = round(entry_px * (1.0 - 0.040), 8)  # 4.0% MTB SL default
                elif bot_enum == BotName.NR7:
                    sl_val = round(entry_px * (1.0 - 0.038), 8)  # 3.8% NR7 SL default
                elif bot_enum == BotName.PPA:
                    sl_val = round(entry_px * (1.0 - 0.040), 8)  # 4.0% PPA SL default
                elif bot_enum == BotName.MRB:
                    sl_val = round(entry_px * (1.0 - 0.038), 8)  # 3.8% MRB SL default
                else:
                    sl_val = round(entry_px * 0.965, 8)  # 3.5% STE SL default
            else:
                sl_val = float(sl_val)

            if tp_val is None:
                if bot_enum == BotName.HDA:
                    if score >= 95:
                        tp_val = round(entry_px * 1.21, 8)  # 21.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.135, 8)  # 13.5% Elite target
                    else:
                        tp_val = round(entry_px * 1.068, 8)  # 6.8% Standard target
                elif bot_enum == BotName.VCP:
                    if score >= 95:
                        tp_val = round(entry_px * 1.22, 8)  # 22.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.13, 8)  # 13.0% Elite target
                    else:
                        tp_val = round(entry_px * 1.066, 8)  # 6.6% Standard target
                elif bot_enum == BotName.BBS:
                    if score >= 95:
                        tp_val = round(entry_px * 1.25, 8)  # 25.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.14, 8)  # 14.0% Elite target
                    else:
                        tp_val = round(entry_px * 1.062, 8)  # 6.2% Standard target
                elif bot_enum == BotName.MTB:
                    if score >= 95:
                        tp_val = round(entry_px * 1.22, 8)  # 22.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.12, 8)  # 12.0% Elite target
                    else:
                        tp_val = round(entry_px * 1.055, 8)  # 5.5% Standard target
                elif bot_enum == BotName.NR7:
                    if score >= 95:
                        tp_val = round(entry_px * 1.20, 8)  # 20.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.11, 8)  # 11.0% Elite target
                    else:
                        tp_val = round(entry_px * 1.048, 8)  # 4.8% Standard target
                elif bot_enum == BotName.PPA:
                    if score >= 95:
                        tp_val = round(entry_px * 1.21, 8)  # 21.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.115, 8)  # 11.5% Elite target
                    else:
                        tp_val = round(entry_px * 1.050, 8)  # 5.0% Standard target
                elif bot_enum == BotName.MRB:
                    if score >= 95:
                        tp_val = round(entry_px * 1.20, 8)  # 20.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.11, 8)  # 11.0% Elite target
                    else:
                        tp_val = round(entry_px * 1.048, 8)  # 4.8% Standard target
                else:
                    if score >= 95:
                        tp_val = round(entry_px * 1.20, 8)  # 20.0% Super Elite target
                    elif score >= 90:
                        tp_val = round(entry_px * 1.10, 8)  # 10.0% Elite target
                    else:
                        tp_val = round(entry_px * 1.05, 8)  # 5.0% Standard target
            else:
                tp_val = float(tp_val)

            pos = await self.position_manager.open_position(
                bot=bot_enum,
                coin=coin_val,
                pair=pair_val,
                entry_price=entry_px,
                qty=qty_val,
                mode=BotMode.PAPER,
                stop_loss=sl_val,
                take_profit=tp_val,
                signal_id=signal_id,
                market_regime=str(payload.get("market_state") or "") or None,
            )
            if not pos or not getattr(pos, "id", None):
                raise RuntimeError("PositionManager.open_position returned an empty or invalid position object")

            result["position_id"] = pos.id
            logger.info(
                "Paper position successfully opened & persisted: id=%s for %s (%s) @ %.4f (TP: %.4f, SL: %.4f)",
                pos.id,
                pos.coin,
                pos.pair,
                pos.entry_price,
                tp_val,
                sl_val,
            )
        except Exception as pos_err:
            logger.error("Failed to persist paper position in PositionManager: %s", pos_err, exc_info=True)
            result["success"] = False
            result["error"] = f"POSITION_PERSISTENCE_FAILED: {pos_err}"
            result["position_persist_error"] = str(pos_err)

        return result
