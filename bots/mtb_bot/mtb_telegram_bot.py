"""
PROJECT-ALPHA MTB Telegram Bot
FIX: MTB Telegram Bot - V1

Commands:
- /status - MTB bot operational status
- /positions - Current open positions
- /stats - Trading statistics
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes
)

# MTB data imports
from . import storage
from .config import TRADE_AMOUNT

logger = logging.getLogger("mtb_telegram_bot")

# ============================================================
# CONFIGURATION
# ============================================================

# Multi-bot configuration - V1
# ENV: MTB_BOT_TOKEN - Telegram bot token for MTB (fallback: BOT_TOKEN)
MTB_BOT_TOKEN = os.getenv("MTB_BOT_TOKEN", os.getenv("BOT_TOKEN", ""))
# ENV: MTB_CHAT_ID - Chat ID for MTB notifications (fallback: TELEGRAM_CHAT_ID)
MTB_CHAT_ID = os.getenv("MTB_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))

# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: MTB Telegram Bot - V1"""
    await update.message.reply_text(
        "📈 *PROJECT-ALPHA MTB Bot*\n"
        "_MACD Trend Bounce Trading_\n\n"
        "Commands:\n"
        "/status - MTB operational status\n"
        "/positions - Current open positions\n"
        "/stats - Trading statistics\n"
        "/help - Show this help",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: MTB Telegram Bot - V1"""
    await start_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: MTB Telegram Bot - V1
    Show MTB bot operational status.
    """
    try:
        # Get snapshot data
        data = storage.snapshot()
        
        status = data.get("status", "UNKNOWN")
        open_positions = data.get("open_positions", [])
        closed_trades = data.get("closed_trades", [])
        daily_pnl = data.get("daily_pnl", 0)
        total_pnl = data.get("total_pnl", 0)
        cash_balance = data.get("cash_balance", 0)
        trade_amount = data.get("trade_amount", TRADE_AMOUNT)
        watchlist = data.get("watchlist", [])
        last_updated = data.get("last_updated", "Unknown")
        
        # Status emoji
        if status in ("ONLINE", "INTEGRATED"):
            status_emoji = "🟢"
        elif status in ("PAUSED", "DISABLED"):
            status_emoji = "🟡"
        else:
            status_emoji = "🔴"
        
        # P&L emoji
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        
        message = (
            f"{status_emoji} *MTB Status: {status}*\n\n"
            f"📊 *Overview*\n"
            f"├ Open Positions: `{len(open_positions)}`\n"
            f"├ Total Trades: `{len(closed_trades)}`\n"
            f"├ Trade Amount: `₹{trade_amount:.0f}`\n"
            f"├ Scanner Coins: `{len(watchlist)}`\n"
            f"└ Cash Balance: `₹{cash_balance:,.2f}`\n\n"
            f"{pnl_emoji} *Performance*\n"
            f"├ Today's P&L: `₹{daily_pnl:,.2f}`\n"
            f"└ Total P&L: `₹{total_pnl:,.2f}`\n\n"
            f"🕐 Last Update: `{last_updated or 'N/A'}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Status command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: MTB Telegram Bot - V1
    Show current open positions.
    """
    try:
        positions = storage.load_positions()
        open_positions = [p for p in positions if str(p.get("status", "")).upper() == "OPEN"]
        
        if not open_positions:
            await update.message.reply_text("📭 No open positions.")
            return
        
        message = "📊 *MTB Open Positions*\n\n"
        total_invested = 0
        total_pnl = 0
        
        for pos in open_positions:
            coin = pos.get("coin", "???")
            entry_price = pos.get("entry_price", pos.get("buy_price", 0))
            current_price = pos.get("current_price", entry_price)
            qty = pos.get("qty", 0)
            amount = pos.get("amount", pos.get("invested", 0))
            
            # Calculate P&L
            if qty > 0 and current_price > 0:
                current_value = qty * current_price
                pnl = current_value - amount
                pnl_pct = (pnl / amount * 100) if amount > 0 else 0
            else:
                pnl = 0
                pnl_pct = 0
            
            total_invested += amount
            total_pnl += pnl
            
            # P&L emoji
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            
            message += (
                f"{pnl_emoji} *{coin}*\n"
                f"├ Entry: `₹{entry_price:.4f}`\n"
                f"├ Current: `₹{current_price:.4f}`\n"
                f"├ Qty: `{qty:.4f}`\n"
                f"└ P&L: `₹{pnl:.2f} ({pnl_pct:+.1f}%)`\n\n"
            )
        
        # Summary
        total_emoji = "🟢" if total_pnl >= 0 else "🔴"
        message += (
            f"━━━━━━━━━━━━━━━\n"
            f"{total_emoji} *Summary*\n"
            f"├ Positions: `{len(open_positions)}`\n"
            f"├ Invested: `₹{total_invested:,.2f}`\n"
            f"└ Unrealized: `₹{total_pnl:,.2f}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Positions command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: MTB Telegram Bot - V1
    Show trading statistics.
    """
    try:
        stats = storage.load_stats()
        trades = storage.load_trades()
        
        # Filter closed trades
        closed_trades = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
        
        # Calculate statistics
        total_trades = len(closed_trades)
        wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed_trades if t.get("pnl", 0) < 0)
        breakeven = total_trades - wins - losses
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # P&L statistics
        total_pnl = sum(t.get("pnl", 0) for t in closed_trades)
        winning_pnl = sum(t.get("pnl", 0) for t in closed_trades if t.get("pnl", 0) > 0)
        losing_pnl = sum(t.get("pnl", 0) for t in closed_trades if t.get("pnl", 0) < 0)
        
        avg_win = winning_pnl / wins if wins > 0 else 0
        avg_loss = losing_pnl / losses if losses > 0 else 0
        
        # Profit factor
        profit_factor = abs(winning_pnl / losing_pnl) if losing_pnl != 0 else float('inf')
        pf_display = f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞"
        
        # Daily stats from storage
        daily_pnl = stats.get("daily_pnl", 0)
        
        # Performance emoji
        perf_emoji = "🏆" if win_rate >= 60 else "📊" if win_rate >= 50 else "📉"
        
        message = (
            f"{perf_emoji} *MTB Statistics*\n\n"
            f"📊 *Trade Summary*\n"
            f"├ Total Trades: `{total_trades}`\n"
            f"├ Wins: `{wins}` 🟢\n"
            f"├ Losses: `{losses}` 🔴\n"
            f"├ Breakeven: `{breakeven}`\n"
            f"└ Win Rate: `{win_rate:.1f}%`\n\n"
            f"💰 *P&L Analysis*\n"
            f"├ Total P&L: `₹{total_pnl:,.2f}`\n"
            f"├ Today's P&L: `₹{daily_pnl:,.2f}`\n"
            f"├ Avg Win: `₹{avg_win:,.2f}`\n"
            f"├ Avg Loss: `₹{avg_loss:,.2f}`\n"
            f"└ Profit Factor: `{pf_display}`\n\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Stats command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


# ============================================================
# BOT INITIALIZATION
# ============================================================

def create_mtb_bot():
    """
    FIX: MTB Telegram Bot - V1
    Create and configure the MTB telegram bot.
    """
    if not MTB_BOT_TOKEN:
        logger.warning("MTB_BOT_TOKEN not set - MTB Telegram bot disabled")
        return None
    
    try:
        app = ApplicationBuilder().token(MTB_BOT_TOKEN).build()
        
        # Register command handlers
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        app.add_handler(CommandHandler("positions", positions_cmd))
        app.add_handler(CommandHandler("stats", stats_cmd))
        
        logger.info("MTB Telegram bot initialized")
        return app
        
    except Exception as e:
        logger.error("Failed to create MTB bot: %s", e)
        return None


async def run_mtb_bot():
    """
    FIX: MTB Telegram Bot - V1
    Run the MTB telegram bot.
    """
    app = create_mtb_bot()
    if app is None:
        return
    
    logger.info("Starting MTB Telegram bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    while True:
        await asyncio.sleep(3600)


# ============================================================
# LIFESPAN HOOKS — used by app.py alongside scanner/vgx/pmb bots
# ============================================================

_MTB_TG_APP = None


async def startup_event() -> None:
    global _MTB_TG_APP
    app = create_mtb_bot()
    if app is None:
        logger.warning("MTB Telegram bot not started — no token")
        return
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    _MTB_TG_APP = app
    logger.info("MTB Telegram bot started")


async def shutdown_event() -> None:
    global _MTB_TG_APP
    if _MTB_TG_APP is not None:
        await _MTB_TG_APP.updater.stop()
        await _MTB_TG_APP.stop()
        await _MTB_TG_APP.shutdown()
        _MTB_TG_APP = None
        logger.info("MTB Telegram bot stopped")


# ============================================================
# STANDALONE ENTRY POINT
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    )
    
    asyncio.run(run_mtb_bot())
