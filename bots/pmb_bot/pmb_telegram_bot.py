"""
PROJECT-ALPHA PMB Telegram Bot
FIX: PMB Telegram Bot - V1

Commands:
- /status - PMB bot operational status
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

# PMB data imports
from . import storage
from .config import BASE_BUY, DIP_BUY

logger = logging.getLogger("pmb_telegram_bot")

# ============================================================
# CONFIGURATION
# ============================================================

# Multi-bot configuration - V1
# ENV: PMB_BOT_TOKEN - Telegram bot token for PMB (fallback: BOT_TOKEN)
PMB_BOT_TOKEN = os.getenv("PMB_BOT_TOKEN", os.getenv("BOT_TOKEN", ""))
# ENV: PMB_CHAT_ID - Chat ID for PMB notifications (fallback: TELEGRAM_CHAT_ID)
PMB_CHAT_ID = os.getenv("PMB_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))

# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: PMB Telegram Bot - V1"""
    await update.message.reply_text(
        "💰 *PROJECT-ALPHA PMB Bot*\n"
        "_Price Movement Bot - DCA Strategy_\n\n"
        "Commands:\n"
        "/status - PMB operational status\n"
        "/positions - Current open positions\n"
        "/stats - Trading statistics\n"
        "/help - Show this help",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: PMB Telegram Bot - V1"""
    await start_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: PMB Telegram Bot - V1
    Show PMB bot operational status.
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
        watchlist = data.get("watchlist", [])
        last_updated = data.get("last_updated", "Unknown")
        
        # PMB enabled check
        pmb_enabled = os.getenv("PMB_ENABLED", "false").lower() == "true"
        
        # Status emoji
        if status in ("ONLINE", "INTEGRATED") and pmb_enabled:
            status_emoji = "🟢"
            status_text = status
        elif not pmb_enabled:
            status_emoji = "🟡"
            status_text = "DISABLED"
        elif status in ("PAUSED",):
            status_emoji = "🟡"
            status_text = status
        else:
            status_emoji = "🔴"
            status_text = status
        
        # P&L emoji
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        
        message = (
            f"{status_emoji} *PMB Status: {status_text}*\n\n"
            f"📊 *Overview*\n"
            f"├ Open Positions: `{len(open_positions)}`\n"
            f"├ Total Trades: `{len(closed_trades)}`\n"
            f"├ Base Buy: `₹{BASE_BUY:.0f}`\n"
            f"├ Dip Buy: `₹{DIP_BUY:.0f}`\n"
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
    FIX: PMB Telegram Bot - V1
    Show current open positions.
    """
    try:
        positions = storage.load_positions()
        open_positions = [p for p in positions if str(p.get("status", "")).upper() == "OPEN"]
        
        if not open_positions:
            await update.message.reply_text("📭 No open positions.")
            return
        
        message = "📊 *PMB Open Positions*\n\n"
        total_invested = 0
        total_pnl = 0
        
        for pos in open_positions:
            coin = pos.get("coin", "???")
            entry_price = pos.get("entry_price", pos.get("buy_price", pos.get("avg_price", 0)))
            current_price = pos.get("current_price", entry_price)
            qty = pos.get("qty", pos.get("quantity", 0))
            amount = pos.get("amount", pos.get("invested", 0))
            dip_count = pos.get("dip_count", pos.get("dip_buys", 0))
            
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
                f"├ Avg Entry: `₹{entry_price:.4f}`\n"
                f"├ Current: `₹{current_price:.4f}`\n"
                f"├ Invested: `₹{amount:.2f}`\n"
                f"├ Dip Buys: `{dip_count}`\n"
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
    FIX: PMB Telegram Bot - V1
    Show trading statistics.
    """
    try:
        stats = storage.load_stats()

        # Open trade log != closed trade history: storage.load_trades() returns
        # every event (BASE_BUY, DIP_BUY_N, exits, ...). Stats must only be
        # computed over genuinely completed trades, so use get_closed_trades()
        # instead of slicing the raw log — otherwise entry rows with pnl=0
        # would be double-counted as "breakeven" trades.
        closed_trades = storage.get_closed_trades()[-50:]  # Last 50 completed trades
        
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
        
        # DCA statistics
        total_dip_buys = sum(t.get("dip_count", 0) for t in closed_trades)
        
        # Daily stats from storage
        daily_pnl = stats.get("daily_pnl", 0)
        
        # Performance emoji
        perf_emoji = "🏆" if win_rate >= 60 else "📊" if win_rate >= 50 else "📉"
        
        message = (
            f"{perf_emoji} *PMB Statistics*\n\n"
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
            f"📈 *DCA Stats*\n"
            f"├ Total Dip Buys: `{total_dip_buys}`\n"
            f"├ Base Amount: `₹{BASE_BUY:.0f}`\n"
            f"└ Dip Amount: `₹{DIP_BUY:.0f}`\n\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Stats command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


# ============================================================
# BOT INITIALIZATION
# ============================================================

def create_pmb_bot():
    """
    FIX: PMB Telegram Bot - V1
    Create and configure the PMB telegram bot.
    """
    if not PMB_BOT_TOKEN:
        logger.warning("PMB_BOT_TOKEN not set - PMB Telegram bot disabled")
        return None
    
    try:
        app = ApplicationBuilder().token(PMB_BOT_TOKEN).build()
        
        # Register command handlers
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        app.add_handler(CommandHandler("positions", positions_cmd))
        app.add_handler(CommandHandler("stats", stats_cmd))
        
        logger.info("PMB Telegram bot initialized")
        return app
        
    except Exception as e:
        logger.error("Failed to create PMB bot: %s", e)
        return None


async def run_pmb_bot():
    """
    FIX: PMB Telegram Bot - V1
    Run the PMB telegram bot.
    """
    app = create_pmb_bot()
    if app is None:
        return
    
    logger.info("Starting PMB Telegram bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    while True:
        await asyncio.sleep(3600)


# ============================================================
# LIFESPAN HOOKS — used by app.py alongside scanner/vgx bots
# ============================================================

_PMB_TG_APP = None


async def startup_event() -> None:
    global _PMB_TG_APP
    app = create_pmb_bot()
    if app is None:
        logger.warning("PMB Telegram bot not started — no token")
        return
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    _PMB_TG_APP = app
    logger.info("PMB Telegram bot started")


async def shutdown_event() -> None:
    global _PMB_TG_APP
    if _PMB_TG_APP is not None:
        await _PMB_TG_APP.updater.stop()
        await _PMB_TG_APP.stop()
        await _PMB_TG_APP.shutdown()
        _PMB_TG_APP = None
        logger.info("PMB Telegram bot stopped")


# ============================================================
# STANDALONE ENTRY POINT
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    )
    
    asyncio.run(run_pmb_bot())
