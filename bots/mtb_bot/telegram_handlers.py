"""
Telegram command handlers for MTB Bot.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from . import storage
from .config import BOT_MODE, BOT_NAME, BOT_VERSION, TRADE_AMOUNT
from .trading_engine import close_position, open_paper_position


def _fmt_money(value: float) -> str:
    return f"{float(value):.2f}"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"{BOT_NAME} Bot v{BOT_VERSION} online.\n"
        "Commands: /status /buy /sell /tradeamount"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    snap = storage.snapshot()
    await update.message.reply_text(
        f"MTB Status: {snap['status']}\n"
        f"Mode: {BOT_MODE}\n"
        f"Open Positions: {len(snap['open_positions'])}\n"
        f"Closed Trades: {len(snap['closed_trades'])}\n"
        f"Daily PnL: {_fmt_money(snap['daily_pnl'])}\n"
        f"Trade Amount: {_fmt_money(snap['trade_amount'])}\n"
        f"Cash: {_fmt_money(snap['cash_balance'])}"
    )


async def tradeamount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"MTB TRADE_AMOUNT is {_fmt_money(TRADE_AMOUNT)}.\n"
        "Every BUY uses exactly this amount."
    )


async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /buy BTC 65000")
        return
    coin = context.args[0].upper().replace("USDT", "")
    try:
        price = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid price.")
        return
    signal = {
        "coin": coin,
        "symbol": f"{coin}USDT",
        "action": "BUY",
        "entry_price": price,
        "score": 100,
        "source": "MTB_TELEGRAM",
    }
    result = open_paper_position(signal)
    if result.get("ok"):
        pos = result["position"]
        await update.message.reply_text(
            f"MTB BUY opened: {pos['symbol']}\n"
            f"Amount: {_fmt_money(pos['trade_amount'])}\n"
            f"Entry: {pos['entry_price']}"
        )
        return
    await update.message.reply_text(f"MTB BUY blocked: {result.get('reason')}")


async def sell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /sell BTC 68000")
        return
    coin = context.args[0].upper().replace("USDT", "")
    try:
        price = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid price.")
        return
    result = close_position(f"{coin}USDT", price, reason="TELEGRAM")
    if result.get("ok"):
        pos = result["position"]
        await update.message.reply_text(
            f"MTB SELL closed: {pos['symbol']}\n"
            f"PnL: {_fmt_money(pos.get('pnl', 0))}\n"
            f"Return: {pos.get('return_pct', 0)}%"
        )
        return
    await update.message.reply_text(f"MTB SELL blocked: {result.get('reason')}")

