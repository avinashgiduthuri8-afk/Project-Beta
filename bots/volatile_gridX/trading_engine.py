"""
PROJECT-ALPHA Trading Engine
Handles BUY execution and position management.
"""

import logging
import threading
import time
from datetime import datetime

from . import config
from . import storage
from .market_data import get_cached_price_safe

logger = logging.getLogger("vgx.trading_engine")

# ============================================================
# CONCURRENCY LOCK
# One lock protects all balance + position mutations for VGX.
# Both buy_position() and close_position() must hold this lock
# for the entire check → mutate → save sequence.
# ============================================================

_TRADE_LOCK = threading.Lock()


# ============================================================
# CREATE POSITION  (pure data builder — no side effects)
# ============================================================

def open_position(
    coin: str,
    price: float,
    amount: float,
    source: str = "SCANNER"
) -> dict:
    qty = amount / price
    return {
        "coin":             coin,
        "buy_price":        price,
        "qty":              qty,
        "amount":           amount,
        "time":             time.time(),
        "peak":             price,
        "trailing_active":  False,
        "trade_source":     source,
    }


# ============================================================
# BUY POSITION  — atomic critical section
# ============================================================

def buy_position(
    coin: str,
    price: float,
    amount: float,
    source: str = "SCANNER",
) -> bool:
    """
    Open a paper position atomically.

    The entire sequence — duplicate check, balance check, balance
    deduction, position insertion — runs inside _TRADE_LOCK so that
    two concurrent BUY signals for the same coin cannot both pass
    the duplicate check and both deduct the balance.

    Returns True on success, False on any guard failure (including
    duplicate, which is logged but never raises).
    """
    if amount <= 100:
        return False
    if price <= 0:
        return False

    pos_key = f"{coin}_{source}"

    logger.debug("buy_position: acquiring lock for %s", pos_key)
    with _TRADE_LOCK:
        logger.debug("buy_position: lock acquired for %s", pos_key)

        # ── All checks inside the lock ────────────────────────
        # Duplicate check by coin (not pos_key) so the same coin cannot be
        # bought twice under different sources, double-counting capital.
        if any(p["coin"] == coin for p in storage.positions.values()):
            logger.info(
                "Duplicate position prevented: %s already has an open position (lock held)",
                coin,
            )
            return False

        if storage.virtual_balance < amount:
            logger.info(
                "buy_position: insufficient balance for %s (%.2f < %.2f)",
                pos_key, storage.virtual_balance, amount,
            )
            return False

        # ── Atomic mutation ───────────────────────────────────
        storage.virtual_balance -= amount
        storage.positions[pos_key] = open_position(coin, price, amount, source)
        # save_data() is called by the caller (paper_execute_signal) after
        # appending the trade log entry — keep it outside the lock to avoid
        # holding the lock during I/O.

    logger.debug("buy_position: lock released for %s", pos_key)
    logger.info("Position opened: %s @ %.4f  amount=%.2f", pos_key, price, amount)
    return True


# ============================================================
# CLOSE POSITION  — atomic critical section
# ============================================================

def close_position(
    pos_key: str,
    current_price: float,
) -> tuple:
    """
    Close a position atomically.

    Returns (receive_amount, pnl, source) on success, (0, 0, None) if
    the position doesn't exist (e.g. already closed by a concurrent call).
    """
    logger.debug("close_position: acquiring lock for %s", pos_key)
    with _TRADE_LOCK:
        logger.debug("close_position: lock acquired for %s", pos_key)

        if pos_key not in storage.positions:
            logger.info(
                "Duplicate close prevented: %s not found (lock held)", pos_key
            )
            return 0, 0, None

        pos            = storage.positions[pos_key]
        qty            = pos["qty"]
        receive_amount = qty * current_price
        pnl            = receive_amount - pos["amount"]
        source         = pos["trade_source"]

        storage.virtual_balance += receive_amount
        del storage.positions[pos_key]

    logger.debug("close_position: lock released for %s", pos_key)
    logger.info(
        "Position closed: %s  receive=%.4f  pnl=%.4f", pos_key, receive_amount, pnl
    )
    return receive_amount, pnl, source


# ============================================================
# PAPER EXECUTION
# ============================================================

def emergency_close_all(reason: str = "EMERGENCY") -> dict:
    """
    Close every open position using the existing _TRADE_LOCK via close_position().
    Safe for concurrent calls with buy_position() / close_position() because each
    close_position() acquires the lock independently — do NOT hold _TRADE_LOCK
    across the whole loop (that would deadlock against close_position's own acquire).

    Returns a summary dict with the same shape as trading_engine_v2.emergency_close_all
    for compatibility with anything reading the result.
    """
    logger.critical("emergency_close_all triggered: %s", reason)
    closed = 0
    failed = 0
    total_pnl = 0.0
    details: list = []

    # Snapshot current keys atomically so the loop doesn't iterate a mutating dict.
    with _TRADE_LOCK:
        pos_keys = list(storage.positions.keys())

    for pos_key in pos_keys:
        try:
            # Read position data without the lock; close_position() will handle
            # the "already gone" case safely (returns (0, 0, None)).
            pos = storage.positions.get(pos_key)
            if pos is None:
                continue  # closed by a concurrent call between snapshot and here

            coin = pos["coin"]
            price = get_cached_price_safe(coin)
            if price <= 0:
                # Conservative fallback: same assumption as trading_engine_v2
                price = pos["buy_price"] * 0.95

            receive_amount, pnl, source = close_position(pos_key, price)
            if source is not None:  # None only when pos was already gone
                closed += 1
                total_pnl += pnl
                details.append({
                    "pos_key": pos_key,
                    "coin":    coin,
                    "pnl":     round(pnl, 4),
                    "price":   round(price, 4),
                    "source":  source,
                })
        except Exception as exc:
            logger.error("emergency_close_all: failed to close %s: %s", pos_key, exc)
            failed += 1
            details.append({"pos_key": pos_key, "error": str(exc)})

    storage.save_data()
    logger.critical(
        "emergency_close_all complete: closed=%d failed=%d total_pnl=%.4f reason=%s",
        closed, failed, total_pnl, reason,
    )
    return {
        "closed":    closed,
        "failed":    failed,
        "total_pnl": round(total_pnl, 4),
        "reason":    reason,
        "details":   details,
    }


def paper_execute_signal(signal: dict) -> tuple:
    if signal["action"] != "BUY":
        return False, "BUY Only"

    coin   = signal["coin"]
    source = signal.get("source", "SCANNER")
    price  = get_cached_price_safe(coin)

    if price <= 0:
        return False, "Invalid Price"

    amount = config.TRADE_AMOUNT
    if amount <= 100:
        return False, "Trade Amount Must Be Above 100"

    success = buy_position(coin, price, amount, source)
    if not success:
        return False, "Duplicate Position or Balance Low"

    trade_entry = {
        "time":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "coin":         coin,
        "action":       f"BUY [{source}]",
        "price":        round(price, 2),
        "amount":       round(amount, 2),
        "pnl":          0,
        "trade_source": source,
    }
    storage.trade_log.append(trade_entry)
    storage.save_data()

    return True, f"{coin} BUY Executed"

