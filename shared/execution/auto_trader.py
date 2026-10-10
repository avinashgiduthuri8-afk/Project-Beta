"""
PROJECT-ALPHA Auto Trade Dispatcher & Strategy Router.

Subscribes to SIGNAL_GENERATED events on the EventBus, maps signals to isolated
production bot strategies (STE, HDA, VCP, BBS), evaluates sub-account capital limits,
enforces order book precision & min ₹100 notional rules, and dispatches HMAC-signed orders.
"""

from __future__ import annotations

import inspect
import threading
from datetime import datetime, timezone
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.types import BotName, Signal
from execution.trading.precision_rules import (
    extract_base_coin,
    normalize_price,
    round_price,
    round_qty,
    round_qty_up,
    validate_order_notional,
)
from execution.trading.subaccount_manager import (
    CoinDCXSubAccountManager,
)

logger = get_logger("execution.auto_trader")


class AutoTradeRouter:
    """
    Auto Trade Dispatcher & Strategy Router.
    Enforces signal idempotency, cross-strategy asset locking, precision rules, and HMAC-signed sub-account order dispatch.
    """

    def __init__(
        self,
        bus: EventBus,
        subaccount_manager: CoinDCXSubAccountManager | None = None,
        dry_run: bool = False,
        position_repo: Any | None = None,
    ) -> None:
        self._bus = bus
        self._subaccount_manager = subaccount_manager or CoinDCXSubAccountManager()
        self.dry_run = dry_run
        self._position_repo = position_repo
        self._processed_idempotency_keys: set[str] = set()
        self._lock = threading.RLock()

    def map_signal_to_bot(self, signal_data: dict[str, Any]) -> BotName:
        """
        Map incoming signal payload/dataclass to isolated production strategy (STE, HDA, VCP, BBS).
        """
        bot_hint = (
            signal_data.get("bot")
            or signal_data.get("strategy")
            or signal_data.get("strategy_name")
            or signal_data.get("target_bot")
            or signal_data.get("source_bot")
        )
        if bot_hint:
            bot_str = str(bot_hint).upper()
            for b in BotName:
                if b.value == bot_str or b.value in bot_str:
                    return b
            if "HIGH DELIVERY" in bot_str or "ABSORPTION" in bot_str or "HDA" in bot_str:
                return BotName.HDA
            if "SUPERTREND" in bot_str or "STE" in bot_str:
                return BotName.STE
            if "CONTRACTION" in bot_str or "VCP" in bot_str:
                return BotName.VCP
            if "BOLLINGER" in bot_str or "BBS" in bot_str or "SQUEEZE" in bot_str:
                return BotName.BBS
            if "MOMENTUM TREND" in bot_str or "MTB" in bot_str:
                return BotName.MTB
            if "NARROW RANGE" in bot_str or "NR7" in bot_str:
                return BotName.NR7

        opp = str(signal_data.get("opportunity_type", "")).lower()
        if "absorption" in opp:
            return BotName.HDA
        elif "momentum" in opp or "continuation" in opp:
            return BotName.STE
        elif "squeeze" in opp or "bollinger" in opp or "bbs" in opp:
            return BotName.BBS
        elif "trend_breakout" in opp or "mtb" in opp:
            return BotName.MTB
        elif "narrow_range" in opp or "nr7" in opp or "compression" in opp:
            return BotName.NR7
        elif "pocket_pivot" in opp or "ppa" in opp or "pocket" in opp:
            return BotName.PPA
        elif "mean_reversion" in opp or "mrb" in opp or "bounce" in opp:
            return BotName.MRB
        elif "breakout" in opp or "recovery" in opp:
            return BotName.HDA
        elif "accumulation" in opp or "vcp" in opp or "contraction" in opp:
            return BotName.VCP

        # Default allocation strategy
        return BotName.STE

    def generate_idempotency_key(self, coin: str, signal_id: str) -> str:
        return f"{coin.upper()}::{signal_id}"

    def is_signal_processed(self, idempotency_key: str) -> bool:
        with self._lock:
            return idempotency_key in self._processed_idempotency_keys

    def mark_signal_processed(self, idempotency_key: str) -> None:
        with self._lock:
            self._processed_idempotency_keys.add(idempotency_key)

    async def handle_signal_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        """EventBus handler callback for SIGNAL_GENERATED."""
        await self.handle_signal(payload)

    async def handle_signal(
        self,
        signal: Signal | dict[str, Any],
        dry_run: bool | None = None,
    ) -> dict[str, Any] | None:
        """
        Main signal processing pipeline:
          1. Extract signal fields & generate idempotency key
          2. Check idempotency (reject duplicates)
          3. Map signal to target production bot (STE, HDA, VCP, BBS)
          4. Retrieve sub-account client & calculate trade parameters
          5. Enforce pre-trade precision rules (round price, round qty, min ₹100 notional)
          6. Dispatch order to CoinDCX sub-account or simulate dry-run
        """
        is_dry_run = dry_run if dry_run is not None else self.dry_run

        if isinstance(signal, Signal):
            signal_data = {
                "id": signal.id,
                "coin": signal.coin,
                "pair": signal.pair,
                "opportunity_type": (
                    signal.opportunity_type.value
                    if hasattr(signal.opportunity_type, "value")
                    else str(signal.opportunity_type)
                ),
                "score": signal.score,
                "price": getattr(signal, "price", None)
                or (signal.raw_payload.get("price") if signal.raw_payload else None),
                "signal_price": getattr(signal, "signal_price", None)
                or (
                    signal.raw_payload.get("signal_price")
                    if signal.raw_payload
                    else None
                ),
                "current_price": getattr(signal, "current_price", None)
                or (
                    signal.raw_payload.get("current_price")
                    if signal.raw_payload
                    else None
                ),
                "stop_loss": getattr(signal, "stop_loss", None)
                or (
                    signal.raw_payload.get("stop_loss") if signal.raw_payload else None
                ),
                "take_profit": getattr(signal, "take_profit", None)
                or (
                    signal.raw_payload.get("take_profit")
                    if signal.raw_payload
                    else None
                ),
                "ticker_timestamp": getattr(signal, "ticker_timestamp", None)
                or (
                    signal.raw_payload.get("ticker_timestamp")
                    if signal.raw_payload
                    else None
                ),
                "timestamp": getattr(signal, "timestamp", None)
                or (signal.raw_payload.get("timestamp") if signal.raw_payload else None)
                or signal.generated_at,
                "trade_amount": (
                    signal.raw_payload.get("trade_amount", 200.0)
                    if signal.raw_payload
                    else 200.0
                ),
            }
        elif isinstance(signal, dict):
            signal_data = dict(signal)
        else:
            logger.error(
                "Invalid signal object passed to handle_signal: %s", type(signal)
            )
            return None

        signal_id = str(
            signal_data.get("id") or signal_data.get("signal_id") or "UNKNOWN_SIG"
        )
        coin = str(signal_data.get("coin") or "BTC").upper()
        pair = str(signal_data.get("pair") or f"{coin}/INR").upper().replace("_", "/")

        idempotency_key = self.generate_idempotency_key(coin, signal_id)
        if self.is_signal_processed(idempotency_key):
            logger.warning(
                "Duplicate signal rejected by idempotency filter: %s", idempotency_key
            )
            return {
                "success": False,
                "error": "DUPLICATE_SIGNAL",
                "idempotency_key": idempotency_key,
                "message": f"Signal {idempotency_key} has already been processed.",
            }

        # Persistent signal replay protection: check if a position already exists for this signal
        if self._position_repo and signal_id and signal_id != "UNKNOWN_SIG":
            try:
                if hasattr(self._position_repo, "get_by_signal_id"):
                    existing_pos = await self._position_repo.get_by_signal_id(signal_id)
                    if existing_pos:
                        logger.warning(
                            "Signal %s already executed as position %s (status: %s), rejecting duplicate replay",
                            signal_id,
                            existing_pos.id,
                            existing_pos.status,
                        )
                        return {
                            "success": False,
                            "error": "SIGNAL_ALREADY_EXECUTED",
                            "idempotency_key": idempotency_key,
                            "position_id": existing_pos.id,
                            "message": f"Signal {signal_id} has already been executed as position {existing_pos.id}.",
                        }
            except Exception as sig_chk_err:
                logger.debug("Error checking signal_id in position_repo: %s", sig_chk_err)

        # 1. Ticker Age Guard: Reject if ticker age > 5.0s (STALE_MARKET_DATA)
        raw_ts = (
            signal_data.get("ticker_timestamp")
            or signal_data.get("data_time")
            or signal_data.get("timestamp")
        )
        if raw_ts is not None:
            now_ts = datetime.now(timezone.utc).timestamp()
            try:
                if isinstance(raw_ts, (int, float)):
                    ts_val = float(raw_ts)
                    if ts_val > 1e11:  # Epoch milliseconds
                        ts_val = ts_val / 1000.0
                    age = now_ts - ts_val
                elif isinstance(raw_ts, datetime):
                    ts_val = (
                        raw_ts.timestamp()
                        if raw_ts.tzinfo
                        else raw_ts.replace(tzinfo=timezone.utc).timestamp()
                    )
                    age = now_ts - ts_val
                elif isinstance(raw_ts, str):
                    dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    age = now_ts - dt.timestamp()
                else:
                    age = 0.0

                if age > 5.0:
                    logger.warning(
                        "AutoTradeRouter rejected signal for %s: ticker age %.2fs > 5.0s (STALE_MARKET_DATA)",
                        pair,
                        age,
                    )
                    return {
                        "success": False,
                        "error": "STALE_MARKET_DATA",
                        "idempotency_key": idempotency_key,
                        "message": f"Ticker age {age:.2f}s exceeds maximum allowable 5.0s limit.",
                        "age": age,
                    }
            except Exception as e:
                logger.debug("Error computing ticker age: %s", e)

        # 2. Pre-Trade Slippage Guard: Reject if abs(current_price - signal.price) / signal.price > 0.005 (0.5%)
        sig_px_val = signal_data.get("signal_price")
        curr_px_val = signal_data.get("current_price") or signal_data.get(
            "ticker_price"
        )

        if (
            sig_px_val is None
            and curr_px_val is not None
            and signal_data.get("price") is not None
        ):
            sig_px_val = signal_data.get("price")
        elif (
            sig_px_val is not None
            and curr_px_val is None
            and signal_data.get("price") is not None
        ):
            curr_px_val = signal_data.get("price")
        elif (
            hasattr(signal, "price") and curr_px_val is not None and sig_px_val is None
        ):
            sig_px_val = signal.price

        if sig_px_val is not None and curr_px_val is not None:
            try:
                sig_px_float = float(sig_px_val)
                curr_px_float = float(curr_px_val)
                if sig_px_float > 0.0:
                    slippage_pct = abs(curr_px_float - sig_px_float) / sig_px_float
                    if slippage_pct > 0.005:
                        logger.warning(
                            "AutoTradeRouter rejected signal for %s: slippage %.4f (%.2f%%) > 0.50%% (SLIPPAGE_EXCEEDED)",
                            pair,
                            slippage_pct,
                            slippage_pct * 100,
                        )
                        return {
                            "success": False,
                            "error": "SLIPPAGE_EXCEEDED",
                            "idempotency_key": idempotency_key,
                            "message": f"Pre-trade slippage {slippage_pct * 100:.2f}% exceeds maximum allowable 0.50% (signal: {sig_px_float}, current: {curr_px_float})",
                            "slippage_pct": slippage_pct,
                        }
            except Exception as e:
                logger.debug("Error computing pre-trade slippage: %s", e)

        target_bot = self.map_signal_to_bot(signal_data)

        # Cross-Strategy Single Coin Lock: if coin is already open in any strategy bot, reject
        if self._position_repo is not None:
            try:
                candidate_base = extract_base_coin(coin) or extract_base_coin(pair)
                get_open_coro = self._position_repo.get_open()
                if inspect.isawaitable(get_open_coro):
                    open_positions = await get_open_coro
                else:
                    open_positions = get_open_coro
                for op in open_positions:
                    op_base = extract_base_coin(
                        getattr(op, "coin", "")
                    ) or extract_base_coin(getattr(op, "pair", ""))
                    if candidate_base and op_base and candidate_base == op_base:
                        op_unrealized_pnl = getattr(
                            op, "unrealized_pnl_pct", getattr(op, "unrealized_pnl", 0.0)
                        )
                        op_pyramid_count = getattr(op, "pyramid_count", 0)

                        # Swing Pyramiding Logic: Bypass lock if firmly in profit (>3%)
                        if op_unrealized_pnl > 3.0 and op_pyramid_count < 3:
                            logger.info(
                                "Swing Pyramiding Activated for %s: Position already at +%.2f%% profit. Adding capital tranche %d/3.",
                                candidate_base,
                                op_unrealized_pnl,
                                op_pyramid_count + 1,
                            )
                            # Optional: you can set a flag on the signal here to tell the position manager to merge
                            signal_data["is_pyramid_entry"] = True
                            signal_data["parent_position_id"] = getattr(op, "id", None)
                            break

                        op_bot = getattr(op, "bot", "BOT")
                        op_bot_name = (
                            op_bot.value if hasattr(op_bot, "value") else str(op_bot)
                        )
                        target_bot_name = (
                            target_bot.value
                            if hasattr(target_bot, "value")
                            else str(target_bot)
                        )
                        logger.warning(
                            "AutoTradeRouter rejected signal for %s: active position already exists in strategy %s (target: %s)",
                            candidate_base,
                            op_bot_name,
                            target_bot_name,
                        )
                        return {
                            "success": False,
                            "error": "OPPORTUNITY_LOCKED_ACTIVE_PAIR",
                            "idempotency_key": idempotency_key,
                            "message": f"Asset {candidate_base} already has an active open position in strategy {op_bot_name} (PnL: {op_unrealized_pnl}%). Cross-strategy lock prevents opening in {target_bot_name}.",
                        }
            except Exception as exc:
                logger.debug(
                    "AutoTradeRouter active position cross-strategy check error: %s",
                    exc,
                )

        client = self._subaccount_manager.get_client(target_bot)

        raw_price = signal_data.get("price") or signal_data.get("current_price")
        raw_price = (
            curr_px_val
            or signal_data.get("price")
            or sig_px_val
            or signal_data.get("current_price")
        )
        try:
            price = normalize_price(raw_price)
        except ValueError as exc:
            logger.warning(
                "AutoTradeRouter rejected signal for %s: invalid price '%s': %s",
                pair,
                raw_price,
                exc,
            )
            return {
                "success": False,
                "error": "INVALID_PRICE",
                "idempotency_key": idempotency_key,
                "message": f"Signal has invalid price: {exc}",
            }

        trade_amount_inr = float(
            signal_data.get("trade_amount")
            or signal_data.get("amount")
            or client.config.default_trade_amount_inr
        )

        is_usdt_pair = pair.upper().endswith("/USDT") or pair.upper().endswith("USDT")
        usdt_inr_rate = float(signal_data.get("usdt_inr_rate") or 91.50)

        if is_usdt_pair:
            target_usdt = (
                trade_amount_inr / usdt_inr_rate
                if usdt_inr_rate > 0
                else (trade_amount_inr / 91.50)
            )
            qty = target_usdt / price if price > 0 else 0.0
        else:
            qty = trade_amount_inr / price if price > 0 else 0.0

        # Pre-trade precision validation
        rounded_price = round_price(pair, price)
        rounded_qty = round_qty(pair, qty)
        notional_value = rounded_price * rounded_qty
        notional_inr = (
            (notional_value * usdt_inr_rate) if is_usdt_pair else notional_value
        )

        # Hard ₹200 minimum trading value invariant:
        # If calculated notional is below ₹200.00, round quantity UP to the next valid exchange lot step
        if notional_inr < 200.0:
            rounded_qty = round_qty_up(pair, qty)
            notional_value = rounded_price * rounded_qty
            notional_inr = (
                (notional_value * usdt_inr_rate) if is_usdt_pair else notional_value
            )

        if (
            not validate_order_notional(
                pair,
                rounded_price,
                rounded_qty,
                min_notional=200.0,
                usdt_inr_rate=usdt_inr_rate,
            )
            or notional_inr < 200.0
        ):
            logger.warning(
                "Order rejected by precision gate: notional value INR %.2f < min ₹200.00 for pair %s",
                notional_inr,
                pair,
            )
            return {
                "success": False,
                "error": "ORDER_NOTIONAL_BELOW_MINIMUM",
                "notional_value": notional_inr,
                "message": f"Notional value INR {notional_inr:.2f} is below minimum INR 200.00",
            }

        # Mark signal as processed once validated
        self.mark_signal_processed(idempotency_key)

        if is_dry_run:
            logger.info(
                "[DRY-RUN] AutoTradeRouter mapped signal %s to bot %s for pair %s @ %.4f (Qty: %s, Notional INR: %.2f)",
                signal_id,
                target_bot.value,
                pair,
                rounded_price,
                rounded_qty,
                notional_inr,
            )
            return {
                "success": True,
                "dry_run": True,
                "bot": target_bot.value,
                "subaccount_id": client.subaccount_id,
                "pair": pair,
                "price": rounded_price,
                "qty": rounded_qty,
                "notional": notional_value,
                "idempotency_key": idempotency_key,
            }

        order_result = client.place_order(
            pair=pair,
            side="BUY",
            price=rounded_price,
            qty=rounded_qty,
            client_order_id=f"ORD_{idempotency_key.replace('::', '_')}",
        )
        if inspect.isawaitable(order_result):
            order_result = await order_result

        if order_result.get("success"):
            order_record = order_result.get("order", {})
            order_record["idempotency_key"] = idempotency_key

            # Dual-layer protective stop-loss dispatch if stop_loss provided
            sl_val = signal_data.get("stop_loss")
            if sl_val and float(sl_val) > 0 and not is_dry_run:
                try:
                    sl_stop_px = round_price(pair, float(sl_val))
                    sl_limit_px = round_price(pair, sl_stop_px * 0.995)
                    sl_res = client.place_order(
                        pair=pair,
                        side="sell",
                        price=sl_limit_px,
                        stop_price=sl_stop_px,
                        total_quantity=rounded_qty,
                        order_type="stop_limit",
                    )
                    if inspect.isawaitable(sl_res):
                        sl_res = await sl_res
                    if sl_res.get("success"):
                        order_record["stop_loss_order_id"] = sl_res.get(
                            "exchange_order_id"
                        )
                except Exception as sl_err:
                    logger.warning(
                        "AutoTradeRouter failed to place disaster stop-loss for %s: %s",
                        pair,
                        sl_err,
                    )

            logger.info(
                "Order successfully dispatched via AutoTradeRouter for signal %s",
                signal_id,
            )
            return {
                "success": True,
                "bot": target_bot.value,
                "subaccount_id": client.subaccount_id,
                "order": order_record,
                "idempotency_key": idempotency_key,
            }
        else:
            return order_result
