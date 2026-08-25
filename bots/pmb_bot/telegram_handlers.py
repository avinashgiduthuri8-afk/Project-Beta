"""
Telegram command handlers for PMB Bot.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from . import storage
from .config import BASE_BUY, BOT_MODE, BOT_NAME, BOT_VERSION, DIP_BUY, MAX_DIPS, PARTIAL_SELL
from .trading_engine import execute_stop_loss, open_base_position


def _fmt(value: float) -> str:
    return f"₹{float(value):,.2f}"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"🤖 {BOT_NAME} Bot v{BOT_VERSION} — Price Movement Bot\n"
        f"Mode: {BOT_MODE}\n\n"
        f"Strategy:\n"
        f"  Base Buy: {_fmt(BASE_BUY)}\n"
        f"  Dip Buy:  {_fmt(DIP_BUY)} (max {MAX_DIPS} dips)\n"
        f"  Part Sell:{_fmt(PARTIAL_SELL)}\n\n"
        "Commands: /status /buy /sell"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    snap = storage.snapshot()
    open_pos = snap["open_positions"]
    lines = [
        f"📊 PMB Status: {snap['status']}",
        f"Open Positions: {len(open_pos)}",
        f"Closed Trades: {len(snap['closed_trades'])}",
        f"Daily PnL: {_fmt(snap['daily_pnl'])}",
        f"Total PnL: {_fmt(snap['total_pnl'])}",
        f"Cash: {_fmt(snap['cash_balance'])}",
    ]
    if open_pos:
        lines.append("\n— Open —")
        for p in open_pos[:5]:
            lines.append(
                f"  {p['coin']}  dips={p.get('dip_count',0)}/{MAX_DIPS}"
                f"  sells={p.get('partial_sell_count',0)}"
                f"  qty={float(p.get('total_quantity',0)):.6f}"
            )
    await update.message.reply_text("\n".join(lines))


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
        "coin":        coin,
        "entry_price": price,
        "score":       100,
        "source":      "PMB_TELEGRAM",
    }
    result = open_base_position(signal)
    if result.get("ok"):
        pos = result["position"]
        await update.message.reply_text(
            f"✅ PMB BASE_BUY opened\n"
            f"Coin: {pos['coin']}\n"
            f"Amount: {_fmt(BASE_BUY)}\n"
            f"Entry: {pos['avg_entry_price']}\n"
            f"Next dip: {pos['next_dip_price']}\n"
            f"Next sell: {pos['next_sell_price']}"
        )
    else:
        await update.message.reply_text(f"❌ PMB BUY blocked: {result.get('reason')}")


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
    positions = storage.get_open_positions()
    target = next((p for p in positions if p.get("coin") == coin), None)
    if not target:
        await update.message.reply_text(f"No open PMB position for {coin}.")
        return
    result = execute_stop_loss(target, price)
    if result.get("ok"):
        pos = result["position"]
        await update.message.reply_text(
            f"✅ PMB CLOSED {coin}\n"
            f"Exit: {price}\n"
            f"PnL: {_fmt(result.get('pnl', 0))}"
        )
    else:
        await update.message.reply_text(f"❌ PMB SELL error: {result.get('reason')}")
