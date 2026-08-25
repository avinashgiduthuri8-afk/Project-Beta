"""
PROJECT-ALPHA Telegram Command Handlers
"""

from . import config
from . import storage
from . import analytics
from . import trading_engine
from . import scanner_bridge
from .market_data import get_cached_price_safe


# ============================================================
# START
# ============================================================

async def start_cmd(update, context):

    msg = (
        "🚀 PROJECT-ALPHA Trading Bot Online\n\n"
        "/help - Show Commands\n"
        "/buy <coin>\n"
        "/sell <coin>\n"
        "/tradeamount <amount>\n"
        "/stats\n"
        "/history\n"
        "/analytics"
    )

    await update.message.reply_text(msg)


# ============================================================
# STATUS
# ============================================================

async def status_cmd(update, context):

    open_positions = len(storage.positions)

    closed_trades = len(storage.trade_history)

    msg = (
        "PROJECT-ALPHA Volatile Grid X Status\n\n"
        f"Balance: {round(storage.virtual_balance, 2)}\n"
        f"Open Positions: {open_positions}\n"
        f"Closed Trades: {closed_trades}\n"
        f"Trade Amount: â‚¹{round(config.TRADE_AMOUNT, 2)}\n"
        "Scanner signals drive all trades."
    )

    await update.message.reply_text(msg)


# ============================================================
# HELP
# ============================================================

async def help_cmd(update, context):

    msg = """

📌 COMMANDS

/buy BTC
/sell BTC
/tradeamount 110


/stats
/history
/analytics

/mode
/setmode aggressive

/threshold
/setthreshold 75

"""

    await update.message.reply_text(msg)


# ============================================================
# BUY
# ============================================================

async def buy_cmd(update, context):

    if len(context.args) < 1:

        await update.message.reply_text(

            "Usage:\n/buy BTC"

        )

        return

    coin = context.args[0].upper()

    amount = config.TRADE_AMOUNT

    if amount <= 100:

        await update.message.reply_text(

            "Trade amount must be above 100."

        )

        return

    if amount > storage.virtual_balance:

        await update.message.reply_text(

            "Trade amount exceeds available balance."

        )

        return

    price = get_cached_price_safe(

        coin

    )

    if price <= 0:

        await update.message.reply_text(

            f"❌ No price available for {coin}.\n"

            "Wait for the next market cache update and try again."

        )

        return

    success = (

        trading_engine.buy_position(

            coin,

            price,

            amount,

            source="MANUAL"

        )

    )

    if success:

        analytics.log_trade(

            coin,

            "BUY [MANUAL]",

            price,

            amount,

            pnl=0

        )

        await update.message.reply_text(

            f"✅ Bought {coin}"

        )

    else:

        await update.message.reply_text(

            "❌ Buy Failed"

        )


# ============================================================
# TRADE AMOUNT
# ============================================================

async def tradeamount_cmd(update, context):

    if len(context.args) < 1:

        await update.message.reply_text(

            f"Current Trade Amount: â‚¹{round(config.TRADE_AMOUNT, 2)}"

        )

        return

    try:

        amount = float(context.args[0])

    except ValueError:

        await update.message.reply_text(

            "Usage:\n/tradeamount 110"

        )

        return

    if amount <= 100:

        await update.message.reply_text(

            "Trade amount must be above 100."

        )

        return

    if amount > storage.virtual_balance:

        await update.message.reply_text(

            "Trade amount exceeds available balance."

        )

        return

    config.TRADE_AMOUNT = amount

    await update.message.reply_text(

        f"âœ… Trade Amount Updated: â‚¹{round(config.TRADE_AMOUNT, 2)}"

    )


# ============================================================
# SELL
# ============================================================

async def sell_cmd(update, context):

    if len(context.args) < 1:

        return

    coin = (

        context.args[0]

        .upper()

    )

    key = f"{coin}_MANUAL"

    price = (

        get_cached_price_safe(

            coin

        )

    )

    receive, pnl, source = (

        trading_engine.close_position(

            key,

            price

        )

    )

    analytics.log_trade(

        coin,

        "SELL [MANUAL]",

        price,

        receive,

        pnl

    )

    await update.message.reply_text(

        f"✅ Sold {coin}\n"

        f"PnL ₹{round(pnl,2)}"

    )


# ============================================================
# WATCHLIST
# ============================================================



# ============================================================
# MODE
# ============================================================

async def mode_cmd(

    update,

    context

):

    mode = (

        config.PHASE5["risk"]

        .get(

            "active_profile",

            "MODERATE"

        )

    )

    await update.message.reply_text(

        f"Current Mode:\n{mode}"

    )


# ============================================================
# SET MODE
# ============================================================

async def setmode_cmd(

    update,

    context

):

    if not context.args:

        return

    mode = (

        context.args[0]

        .lower()

    )

    if mode in [

        "safe",

        "moderate",

        "aggressive"

    ]:

        config.PHASE5["risk"][

            "active_profile"

        ] = mode.upper()

        await update.message.reply_text(

            f"✅ Mode = {mode}"

        )


# ============================================================
# THRESHOLD
# ============================================================

async def threshold_cmd(

    update,

    context

):

    score = (

        scanner_bridge

        .signal_threshold()

    )

    await update.message.reply_text(

        f"Signal Threshold:\n{score}"

    )


# ============================================================
# SET THRESHOLD
# ============================================================

async def setthreshold_cmd(

    update,

    context

):

    if not context.args:

        return

    score = int(

        context.args[0]

    )

    config.PHASE5["signals"][

        "min_score"

    ] = score

    await update.message.reply_text(

        f"✅ Threshold Updated\n{score}"

    )
