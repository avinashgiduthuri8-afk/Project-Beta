"""
PROJECT-ALPHA Exit Engine
Handles AUTO SELL, Trailing Stop and Exit Logic
"""

from datetime import datetime

from .config import PHASE5
from . import storage
from .market_data import get_cached_price_safe
from .alerts import dispatch_alert_payload, format_telegram_alert


# ============================================================
# TRAILING STOP
# ============================================================

def trailing_check(current_price, pos):

    buy_price = pos["buy_price"]

    peak = max(

        pos.get("peak", buy_price),

        current_price

    )

    pos["peak"] = peak

    profit_pct = (

        (current_price - buy_price)

        / buy_price

    )

    target = PHASE5["trade"]["target_percent"]

    if profit_pct >= target:

        pos["trailing_active"] = True

    if pos.get("trailing_active", False):

        drop = (

            peak - current_price

        ) / peak

        if drop >= 0.015:

            return True, "TRAILING STOP"

    stop_loss = (

        PHASE5["trade"]

        .get(

            "stop_loss_percent",

            0.05

        )

    )

    if profit_pct <= -stop_loss:

        return True, "STOP LOSS"

    return False, "HOLD"


# ============================================================
# EXIT CHECK
# ============================================================

def exit_check(

    coin,

    current_price,

    pos

):

    return trailing_check(

        current_price,

        pos

    )


# ============================================================
# AUTO SELL LOOP
# ============================================================

def auto_sell():

    closed_trades = []

    for pos_key, pos in list(

        storage.positions.items()

    ):

        coin = pos["coin"]

        current_price = (

            get_cached_price_safe(

                coin

            )

        )

        if current_price <= 0:

            continue

        exit_now, reason = (

            exit_check(

                coin,

                current_price,

                pos

            )

        )

        if not exit_now:

            continue

        qty = pos["qty"]

        invested = pos["amount"]

        source = pos.get(

            "trade_source",

            "SCANNER"

        )

        receive_amount = (

            qty * current_price

        )

        pnl = (

            receive_amount

            - invested

        )

        storage.virtual_balance += (

            receive_amount

        )

        del storage.positions[

            pos_key

        ]

        trade_entry = {

            "time":

                datetime.now()

                .strftime(

                    "%Y-%m-%d %H:%M:%S"

                ),

            "coin": coin,

            "action":

                f"AUTO SELL [{source}]",

            "price":

                round(

                    current_price,

                    2

                ),

            "amount":

                round(

                    receive_amount,

                    2

                ),

            "pnl":

                round(

                    pnl,

                    2

                ),

            "trade_source":

                source,

            "reason":

                reason

        }

        storage.trade_log.append(

            trade_entry

        )

        closed_trades.append(

            trade_entry

        )

        # Telegram alert outside any lock (I/O, never blocks state)
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        alert_msg = format_telegram_alert(
            f"VGX AUTO SELL — {reason}",
            coin,
            "WARNING" if pnl < 0 else "INFO",
            (
                f"Exit: {current_price:,.2f}  Entry: {float(pos.get('buy_price', 0)):,.2f}\n"
                f"PnL: ₹{pnl:,.2f}  Return: {((pnl / invested) * 100):,.2f}%\n"
                f"Qty: {qty:,.6f}  Source: {source}"
            )
        )
        dispatch_alert_payload(alert_msg)

    storage.save_data()

    return closed_trades
