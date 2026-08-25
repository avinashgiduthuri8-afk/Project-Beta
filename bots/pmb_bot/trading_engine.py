"""
PMB Paper Trading Engine — Price Movement Bot.

Strategy
────────
  1. BASE BUY (₹1000) on a valid scanner signal.
  2. DIP BUY  (₹100)  each time price falls DIP_THRESHOLD_PCT% from last_buy_price.
     Up to MAX_DIPS additional dip buys per position.
  3. PARTIAL SELL (₹120 worth) each time price rises PARTIAL_SELL_TRIGGER_PCT% above
     current avg_entry_price.  Sell continues on every subsequent trigger increment.
  4. Full STOP LOSS at STOP_LOSS_PCT% below avg_entry_price.

All operations are paper trades — no real exchange calls.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import scanner_bridge, storage
from .config import (
    BASE_BUY,
    DIP_BUY,
    DIP_THRESHOLD_PCT,
    MAX_DIPS,
    MAX_POSITIONS,
    MAX_SIGNAL_AGE_SECONDS,
    MIN_SIGNAL_SCORE,
    PARTIAL_SELL,
    PARTIAL_SELL_TRIGGER_PCT,
    STOP_LOSS_PCT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger("pmb_bot.trading_engine")

# ── Concurrency lock ──────────────────────────────────────────────────────────
# One lock protects all position + stats + trades mutations for PMB.
# open_base_position, execute_dip_buy, execute_partial_sell, and
# execute_stop_loss all hold _TRADE_LOCK for the entire check→mutate→save
# sequence so concurrent signals cannot double-open or corrupt the balance.
_TRADE_LOCK = threading.Lock()


def _send_tg(text: str) -> None:
    """Fire-and-forget Telegram notification. Non-blocking. Never raises.

    Dispatches the HTTP call to a daemon thread so the asyncio event loop
    and the thread-pool slot used by the calling trading function are never
    held while waiting on the network.  Multiple notifications may overlap
    safely.  Any failure is logged but never propagates to the caller.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    import json as _json
    import urllib.request as _ur

    def _do_send() -> None:
        try:
            url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            body = _json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
            req  = _ur.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            _ur.urlopen(req, timeout=5)
        except Exception:
            logger.exception("Telegram notification failed")

    try:
        t = threading.Thread(target=_do_send, daemon=True)
        t.start()
    except Exception:
        logger.exception("Telegram notification failed to dispatch")


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    code: str
    reason: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_position_exists(coin: str, positions: list[dict]) -> bool:
    return any(
        str(p.get("coin", "")).upper() == coin.upper()
        and str(p.get("status", "")).upper() == "OPEN"
        for p in positions
    )


def validate_signal(
    signal: dict,
    positions: list[dict] | None = None,
    stats: dict | None = None,
) -> ValidationResult:
    positions  = positions if positions is not None else storage.load_positions()
    coin       = str(signal.get("coin", "")).upper().strip()
    price      = float(signal.get("entry_price") or 0)
    score      = float(signal.get("score") or 0)
    age        = scanner_bridge.signal_age_seconds(signal)

    if not coin:
        return ValidationResult(False, "MISSING_COIN", "Signal has no coin.")
    if _open_position_exists(coin, positions):
        return ValidationResult(False, "DUPLICATE_POSITION", f"PMB position already open for {coin}.")
    open_count = sum(1 for p in positions if str(p.get("status", "")).upper() == "OPEN")
    if open_count >= MAX_POSITIONS:
        return ValidationResult(False, "MAX_POSITIONS_REACHED", "PMB max open positions reached.")
    if age is not None and age > MAX_SIGNAL_AGE_SECONDS:
        return ValidationResult(False, "SIGNAL_TOO_OLD", f"Signal is {int(age)}s old.")
    if price <= 0:
        return ValidationResult(False, "INVALID_PRICE", "Signal has invalid entry price.")
    if score < MIN_SIGNAL_SCORE:
        return ValidationResult(False, "SCORE_TOO_LOW", f"Score {score:.0f} below PMB threshold {MIN_SIGNAL_SCORE}.")
    # ── Cash gate (caller may supply a pre-fetched stats dict to avoid extra I/O) ──
    if stats is None:
        stats = storage.load_stats()
    if float(stats.get("cash_balance", 0.0)) < BASE_BUY:
        return ValidationResult(False, "INSUFFICIENT_CASH", "PMB cash balance below BASE_BUY.")
    return ValidationResult(True, "OK", "Signal accepted.")


def open_base_position(signal: dict) -> dict:
    """Open initial BASE_BUY position on scanner signal."""
    # ── Shared risk engine gate (outside lock — pure read) ────────────────────
    try:
        from bots.risk_engine.engine import check_trade_allowed
        decision = check_trade_allowed("PMB", float(BASE_BUY))
        if not decision.allowed:
            logger.info("PMB trade blocked by risk engine: %s — %s", decision.code, decision.reason)
            return {"ok": False, "code": decision.code, "reason": decision.reason}
    except Exception as _re:
        logger.warning("PMB risk engine check failed (%s) — proceeding with local guards", _re)

    logger.debug("open_base_position: acquiring lock for %s", signal.get("coin", "?"))
    with _TRADE_LOCK:
        logger.debug("open_base_position: lock acquired for %s", signal.get("coin", "?"))

        # Re-read positions inside lock so state is current
        positions = storage.load_positions()
        result    = validate_signal(signal, positions)
        if not result.passed:
            return {"ok": False, "code": result.code, "reason": result.reason}

        price  = float(signal["entry_price"])
        amount = float(BASE_BUY)
        qty    = amount / price
        coin   = str(signal.get("coin", "")).upper()
        symbol = f"{coin}USDT"
        now    = utc_now()

        stop_loss_price = round(price * (1 - STOP_LOSS_PCT / 100), 8)
        next_dip_price  = round(price * (1 - DIP_THRESHOLD_PCT / 100), 8)
        next_sell_price = round(price * (1 + PARTIAL_SELL_TRIGGER_PCT / 100), 8)

        position = {
            "id":               f"PMB-{coin}-{int(datetime.now(timezone.utc).timestamp())}",
            "bot":              "PMB",
            "coin":             coin,
            "symbol":           symbol,
            "status":           "OPEN",
            "avg_entry_price":  price,
            "last_buy_price":   price,
            "dip_count":        0,
            "total_invested":   amount,
            "total_quantity":   qty,
            "partial_sell_count": 0,
            "stop_loss_price":  stop_loss_price,
            "next_dip_price":   next_dip_price,
            "next_sell_price":  next_sell_price,
            "entry_time":       now,
            "score":            signal.get("score", 0),
            "market_state":     signal.get("market_state", ""),
            "confidence":       signal.get("confidence", 0),
            "source":           signal.get("source", "PMB_SCANNER"),
        }

        # Atomic: positions → stats → trades
        positions.append(position)
        storage.save_positions(positions)

        def _update_buy_stats(s):
            s["cash_balance"]   = round(float(s.get("cash_balance",   0.0)) - amount, 8)
            s["total_invested"] = round(float(s.get("total_invested", 0.0)) + amount, 8)
        storage.update_stats(_update_buy_stats)

        trades = storage.load_trades()
        trades.append({
            "id":        position["id"],
            "bot":       "PMB",
            "coin":      coin,
            "symbol":    symbol,
            "action":    "BASE_BUY",
            "status":    "OPEN",
            "price":     price,
            "amount":    amount,
            "quantity":  qty,
            "timestamp": now,
            "source":    position["source"],
        })
        storage.save_trades(trades)

    logger.info("Position opened: PMB BASE_BUY %s @ %.6f  amount=%.2f", coin, price, amount)
    logger.debug("open_base_position: lock released for %s", coin)
    _send_tg(
        f"🟢 <b>PMB BASE_BUY</b>\n"
        f"Coin: <b>{coin}</b>\n"
        f"Price: {price:,.2f}  Amount: ₹{amount:.0f}\n"
        f"SL: {position['stop_loss_price']:,.2f}  Next Dip: {position['next_dip_price']:,.2f}\n"
        f"Score: {signal.get('score', 0):.0f}"
    )
    return {"ok": True, "position": position}


def execute_dip_buy(position: dict, current_price: float) -> dict:
    """Add a DIP_BUY to an existing open position."""
    logger.debug("execute_dip_buy: acquiring lock for %s", position.get("coin", "?"))
    with _TRADE_LOCK:
        logger.debug("execute_dip_buy: lock acquired for %s", position.get("coin", "?"))

        if position.get("dip_count", 0) >= MAX_DIPS:
            return {"ok": False, "reason": "MAX_DIPS reached."}
        _pre_stats = storage.load_stats()
        if float(_pre_stats.get("cash_balance", 0.0)) < DIP_BUY:
            return {"ok": False, "reason": "Insufficient cash for dip buy."}

        amount     = float(DIP_BUY)
        qty        = amount / current_price
        prev_qty   = float(position.get("total_quantity", 0))
        prev_cost  = float(position.get("total_invested", 0))
        new_qty    = prev_qty + qty
        new_cost   = prev_cost + amount
        new_avg    = new_cost / new_qty if new_qty > 0 else current_price
        dip_count  = int(position.get("dip_count", 0)) + 1

        position.update({
            "avg_entry_price":  round(new_avg, 8),
            "last_buy_price":   current_price,
            "dip_count":        dip_count,
            "total_invested":   round(new_cost, 8),
            "total_quantity":   round(new_qty, 8),
            "next_dip_price":   round(current_price * (1 - DIP_THRESHOLD_PCT / 100), 8),
            "next_sell_price":  round(new_avg * (1 + PARTIAL_SELL_TRIGGER_PCT / 100), 8),
            "stop_loss_price":  round(new_avg * (1 - STOP_LOSS_PCT / 100), 8),
        })

        positions = storage.load_positions()
        for i, p in enumerate(positions):
            if p.get("id") == position["id"]:
                positions[i] = position
                break
        storage.save_positions(positions)

        def _update_dip_stats(s):
            s["cash_balance"]   = round(float(s.get("cash_balance",   0.0)) - amount, 8)
            s["total_invested"] = round(float(s.get("total_invested", 0.0)) + amount, 8)
        storage.update_stats(_update_dip_stats)

        now = utc_now()
        trades = storage.load_trades()
        trades.append({
            "id":        position["id"],
            "bot":       "PMB",
            "coin":      position["coin"],
            "symbol":    position["symbol"],
            "action":    f"DIP_BUY_{dip_count}",
            "status":    "OPEN",
            "price":     current_price,
            "amount":    amount,
            "quantity":  qty,
            "timestamp": now,
        })
        storage.save_trades(trades)

    logger.info("PMB DIP_BUY #%d: %s @ %.6f  amount=%.2f", dip_count, position["coin"], current_price, amount)
    logger.debug("execute_dip_buy: lock released for %s", position.get("coin", "?"))
    return {"ok": True, "position": position}


def execute_partial_sell(position: dict, current_price: float) -> dict:
    """Sell PARTIAL_SELL worth of position at current_price."""
    logger.debug("execute_partial_sell: acquiring lock for %s", position.get("coin", "?"))
    with _TRADE_LOCK:
        logger.debug("execute_partial_sell: lock acquired for %s", position.get("coin", "?"))

        qty_to_sell = PARTIAL_SELL / current_price
        held_qty    = float(position.get("total_quantity", 0))
        if qty_to_sell >= held_qty:
            qty_to_sell = held_qty

        proceeds        = qty_to_sell * current_price
        avg_cost_sold   = float(position.get("avg_entry_price", current_price)) * qty_to_sell
        pnl             = proceeds - avg_cost_sold
        sell_count      = int(position.get("partial_sell_count", 0)) + 1
        new_qty         = round(held_qty - qty_to_sell, 8)
        new_invested    = round(float(position.get("total_invested", 0)) - avg_cost_sold, 8)
        new_sell_price  = round(current_price * (1 + PARTIAL_SELL_TRIGGER_PCT / 100), 8)

        position.update({
            "total_quantity":     new_qty,
            "total_invested":     max(0, new_invested),
            "partial_sell_count": sell_count,
            "next_sell_price":    new_sell_price,
        })
        if new_qty <= 0 or max(0, new_invested) <= 0:
            position["status"]    = "CLOSED"
            position["exit_time"] = utc_now()

        positions = storage.load_positions()
        for i, p in enumerate(positions):
            if p.get("id") == position["id"]:
                positions[i] = position
                break
        storage.save_positions(positions)

        def _update_sell_stats(s):
            s["cash_balance"] = round(float(s.get("cash_balance", 0.0)) + proceeds, 8)
            s["total_pnl"]    = round(float(s.get("total_pnl",    0.0)) + pnl, 8)
            _today = datetime.now(timezone.utc).date().isoformat()
            if s.get("daily_pnl_date") != _today:
                s["daily_pnl"]      = 0.0
                s["daily_pnl_date"] = _today
            s["daily_pnl"] = round(float(s.get("daily_pnl", 0.0)) + pnl, 8)
        storage.update_stats(_update_sell_stats)

        now = utc_now()
        trades = storage.load_trades()
        trades.append({
            "id":        position["id"],
            "bot":       "PMB",
            "coin":      position["coin"],
            "symbol":    position["symbol"],
            "action":    f"PARTIAL_SELL_{sell_count}",
            "status":    "OPEN" if new_qty > 0 else "CLOSED",
            "price":     current_price,
            "amount":    round(proceeds, 8),
            "quantity":  round(qty_to_sell, 8),
            "pnl":       round(pnl, 8),
            "timestamp": now,
        })
        storage.save_trades(trades)

    logger.info("PMB PARTIAL_SELL #%d: %s @ %.6f  pnl=%.4f", sell_count, position["coin"], current_price, pnl)
    logger.debug("execute_partial_sell: lock released for %s", position.get("coin", "?"))

    # Notify outside the lock (I/O, never blocks state)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    _send_tg(
        f"{pnl_emoji} <b>PMB PARTIAL SELL #{sell_count}</b>\n"
        f"Coin: <b>{position['coin']}</b>\n"
        f"Price: {current_price:,.2f}  Sold Qty: {qty_to_sell:,.6f}\n"
        f"PnL: ₹{pnl:,.2f}  Remaining Qty: {new_qty:,.6f}\n"
        f"Status: {'CLOSED' if new_qty <= 0 else 'OPEN'}"
    )

    return {"ok": True, "position": position, "pnl": pnl}


def execute_stop_loss(position: dict, current_price: float) -> dict:
    """Close entire position at stop loss."""
    logger.debug("execute_stop_loss: acquiring lock for %s", position.get("coin", "?"))
    with _TRADE_LOCK:
        logger.debug("execute_stop_loss: lock acquired for %s", position.get("coin", "?"))

        qty       = float(position.get("total_quantity", 0))
        proceeds  = qty * current_price
        cost      = float(position.get("total_invested", 0))
        pnl       = proceeds - cost
        now       = utc_now()

        position.update({
            "status":       "CLOSED",
            "exit_price":   current_price,
            "exit_time":    now,
            "close_reason": "STOP_LOSS",
            "pnl":          round(pnl, 8),
        })
        positions = storage.load_positions()
        for i, p in enumerate(positions):
            if p.get("id") == position["id"]:
                positions[i] = position
                break
        storage.save_positions(positions)

        def _update_sl_stats(s):
            s["cash_balance"] = round(float(s.get("cash_balance", 0.0)) + proceeds, 8)
            s["total_pnl"]    = round(float(s.get("total_pnl",    0.0)) + pnl, 8)
            _today = datetime.now(timezone.utc).date().isoformat()
            if s.get("daily_pnl_date") != _today:
                s["daily_pnl"]      = 0.0
                s["daily_pnl_date"] = _today
            s["daily_pnl"] = round(float(s.get("daily_pnl", 0.0)) + pnl, 8)
        storage.update_stats(_update_sl_stats)

        trades = storage.load_trades()
        trades.append({
            "id":        position["id"],
            "bot":       "PMB",
            "coin":      position["coin"],
            "symbol":    position["symbol"],
            "action":    "STOP_LOSS",
            "status":    "CLOSED",
            "price":     current_price,
            "amount":    round(proceeds, 8),
            "quantity":  qty,
            "pnl":       round(pnl, 8),
            "timestamp": now,
        })
        storage.save_trades(trades)

    logger.warning("PMB STOP_LOSS: %s @ %.6f  pnl=%.4f", position["coin"], current_price, pnl)
    logger.debug("execute_stop_loss: lock released for %s", position.get("coin", "?"))

    # Notify outside the lock (I/O, never blocks state)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    _send_tg(
        f"{pnl_emoji} <b>PMB STOP LOSS</b>\n"
        f"Coin: <b>{position['coin']}</b>\n"
        f"Exit: {current_price:,.2f}  Avg Entry: {float(position.get('avg_entry_price', 0)):,.2f}\n"
        f"PnL: ₹{pnl:,.2f}  Invested: ₹{cost:,.2f}\n"
        f"Qty: {qty:,.6f}"
    )

    return {"ok": True, "position": position, "pnl": pnl}


async def run_cycle() -> dict[str, Any]:
    """
    Full PMB cycle:
      1. Manage existing open positions (dip buys, partial sells, stop losses).
      2. Open new base positions from scanner signals.
    """
    # ── Named sync helpers (spec: wrap each blocking call in a named function) ─
    def _fetch_current_prices():
        return scanner_bridge.get_current_prices()

    def _fetch_open_positions():
        return storage.get_open_positions()

    def _fetch_signals():
        return scanner_bridge.get_signals()

    def _load_all_positions():
        return storage.load_positions()

    def _load_stats():
        return storage.load_stats()

    # ── Offload all blocking I/O to the thread pool ────────────────────────────
    # Fetch prices and open positions concurrently — both are independent reads.
    logger.debug("[PMB] offloading get_current_prices + get_open_positions to thread")
    current_prices, open_positions = await asyncio.gather(
        asyncio.to_thread(_fetch_current_prices),
        asyncio.to_thread(_fetch_open_positions),
    )
    dip_buys = partial_sells = stop_losses = 0

    for pos in list(open_positions):
        coin  = pos.get("coin", "")
        price = current_prices.get(coin)
        if price is None or price <= 0:
            continue

        sl_price   = float(pos.get("stop_loss_price",  0))
        next_dip   = float(pos.get("next_dip_price",   0))
        next_sell  = float(pos.get("next_sell_price",  0))

        if sl_price > 0 and price <= sl_price:
            logger.debug("[PMB] offloading execute_stop_loss (%s) to thread", coin)
            await asyncio.to_thread(execute_stop_loss, pos, price)
            stop_losses += 1
        elif next_sell > 0 and price >= next_sell:
            logger.debug("[PMB] offloading execute_partial_sell (%s) to thread", coin)
            await asyncio.to_thread(execute_partial_sell, pos, price)
            partial_sells += 1
        elif next_dip > 0 and price <= next_dip and int(pos.get("dip_count", 0)) < MAX_DIPS:
            logger.debug("[PMB] offloading execute_dip_buy (%s) to thread", coin)
            await asyncio.to_thread(execute_dip_buy, pos, price)
            dip_buys += 1

    # Fetch signals, positions, and stats concurrently — all are independent reads.
    logger.debug("[PMB] offloading get_signals + load_positions + load_stats to thread")
    raw_signals, positions_snapshot, stats_snap = await asyncio.gather(
        asyncio.to_thread(_fetch_signals),
        asyncio.to_thread(_load_all_positions),
        asyncio.to_thread(_load_stats),
    )
    accepted = rejected = opened = 0
    rejection_reasons: list[dict] = []

    for signal in raw_signals:
        validation = validate_signal(signal, positions_snapshot, stats=stats_snap)
        if not validation.passed:
            rejected += 1
            rejection_reasons.append({"coin": signal.get("coin"), "code": validation.code})
            continue
        logger.debug("[PMB] offloading open_base_position (%s) to thread", signal.get("coin"))
        result = await asyncio.to_thread(open_base_position, signal)
        if result.get("ok"):
            accepted += 1
            opened   += 1
            positions_snapshot.append(result["position"])
        else:
            rejected += 1
            rejection_reasons.append({"coin": signal.get("coin"), "code": result.get("code")})

    return {
        "signals_received":  len(raw_signals),
        "signals_accepted":  accepted,
        "signals_rejected":  rejected,
        "positions_opened":  opened,
        "dip_buys":          dip_buys,
        "partial_sells":     partial_sells,
        "stop_losses":       stop_losses,
        "rejections":        rejection_reasons[-5:],
        "timestamp":         utc_now(),
    }
