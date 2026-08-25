"""
PROJECT-ALPHA VGX Telegram Bot
FIX: VGX Telegram Bot - V1

Commands:
- /status - VGX bot operational status
- /positions - Current open positions
- /equity - Portfolio equity and balance
- /safety - Safety systems status (circuit breaker, kill switches)
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

# VGX data imports
from . import storage
from .config import TRADE_AMOUNT, BOT_TOKEN
from .circuit_breaker import get_breaker_status
from .risk_engine import check_cooldown, market_intelligence
import bots.volatile_gridX.risk_engine as _risk_engine_mod
from .market_data import get_cached_price_safe

logger = logging.getLogger("vgx_telegram_bot")


def _get_risk_status() -> dict:
    """
    Assemble a risk-status dict from the real risk_engine primitives.
    Provides the same shape that safety_cmd() reads so its display logic
    needs no changes.
    """
    cd_active, _ = check_cooldown()
    market = market_intelligence()
    return {
        "market_regime":   market.get("regime", "UNKNOWN"),
        "trading_allowed": not cd_active,
        "cooldown": {
            "active":      cd_active,
            "loss_streak": _risk_engine_mod.loss_streak,
        },
    }


# ============================================================
# CONFIGURATION
# ============================================================

# Multi-bot configuration - V1
# ENV: VGX_BOT_TOKEN - Telegram bot token for VGX (fallback: BOT_TOKEN)
VGX_BOT_TOKEN = os.getenv("VGX_BOT_TOKEN", os.getenv("BOT_TOKEN", ""))
# ENV: VGX_CHAT_ID - Chat ID for VGX notifications (fallback: TELEGRAM_CHAT_ID)
VGX_CHAT_ID = os.getenv("VGX_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))

# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: VGX Telegram Bot - V1"""
    await update.message.reply_text(
        "⚡ *PROJECT-ALPHA VGX Bot*\n\n"
        "Commands:\n"
        "/status - VGX operational status\n"
        "/positions - Current open positions\n"
        "/equity - Portfolio equity & balance\n"
        "/safety - Safety systems status\n"
        "/help - Show this help",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: VGX Telegram Bot - V1"""
    await start_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: VGX Telegram Bot - V1
    Show VGX bot operational status.
    """
    try:
        # Load fresh data
        storage.load_data()
        
        positions = storage.positions or {}
        trade_log = storage.trade_log or []
        balance = storage.virtual_balance
        
        # Get safety status
        breaker = get_breaker_status()
        can_trade = breaker.get("can_trade", False)
        trading_state = breaker.get("trading_state", "UNKNOWN")
        
        # Statistics
        open_count = len(positions)
        total_trades = len(trade_log)
        
        # Recent trades (last 5)
        recent_trades = trade_log[-5:] if trade_log else []
        wins = sum(1 for t in recent_trades if t.get("pnl", 0) > 0)
        
        # Status determination
        if not can_trade:
            status_emoji = "🔴"
            status_text = trading_state
        elif open_count > 0:
            status_emoji = "🟢"
            status_text = "TRADING"
        else:
            status_emoji = "🟡"
            status_text = "IDLE"
        
        message = (
            f"{status_emoji} *VGX Status: {status_text}*\n\n"
            f"📊 *Overview*\n"
            f"├ Open Positions: `{open_count}`\n"
            f"├ Total Trades: `{total_trades}`\n"
            f"├ Trade Amount: `₹{TRADE_AMOUNT}`\n"
            f"└ Balance: `₹{balance:,.2f}`\n\n"
            f"📈 *Recent Performance*\n"
            f"├ Last 5 Trades: `{len(recent_trades)}`\n"
            f"└ Wins: `{wins}/{len(recent_trades)}`\n\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Status command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: VGX Telegram Bot - V1
    Show current open positions.
    """
    try:
        storage.load_data()
        positions = storage.positions or {}
        
        if not positions:
            await update.message.reply_text("📭 No open positions.")
            return
        
        message = "📊 *Open Positions*\n\n"
        total_invested = 0
        total_unrealized = 0
        
        for key, pos in positions.items():
            coin = pos.get("coin", key.split("_")[0])
            buy_price = pos.get("buy_price", 0)
            amount = pos.get("amount", 0)
            qty = pos.get("qty", 0)
            source = pos.get("trade_source", "?")
            
            # Get current price
            current_price = get_cached_price_safe(coin)
            if current_price <= 0:
                current_price = buy_price
            
            # Calculate P&L
            current_value = qty * current_price
            pnl = current_value - amount
            pnl_pct = (pnl / amount * 100) if amount > 0 else 0
            
            total_invested += amount
            total_unrealized += pnl
            
            # P&L emoji
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            
            message += (
                f"{pnl_emoji} *{coin}* ({source})\n"
                f"├ Entry: `₹{buy_price:.4f}`\n"
                f"├ Current: `₹{current_price:.4f}`\n"
                f"├ Invested: `₹{amount:.2f}`\n"
                f"└ P&L: `₹{pnl:.2f} ({pnl_pct:+.1f}%)`\n\n"
            )
        
        # Summary
        total_emoji = "🟢" if total_unrealized >= 0 else "🔴"
        message += (
            f"━━━━━━━━━━━━━━━\n"
            f"{total_emoji} *Total*\n"
            f"├ Invested: `₹{total_invested:,.2f}`\n"
            f"└ Unrealized: `₹{total_unrealized:,.2f}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Positions command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def equity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: VGX Telegram Bot - V1
    Show portfolio equity and balance.
    """
    try:
        storage.load_data()
        
        balance = storage.virtual_balance
        positions = storage.positions or {}
        trade_log = storage.trade_log or []
        
        # Calculate position values
        total_invested = 0
        total_current_value = 0
        
        for key, pos in positions.items():
            coin = pos.get("coin", key.split("_")[0])
            amount = pos.get("amount", 0)
            qty = pos.get("qty", 0)
            
            current_price = get_cached_price_safe(coin)
            if current_price <= 0:
                current_price = pos.get("buy_price", 0)
            
            total_invested += amount
            total_current_value += qty * current_price
        
        unrealized_pnl = total_current_value - total_invested
        
        # Total equity = cash + position value
        total_equity = balance + total_current_value
        
        # Realized P&L from trade log
        realized_pnl = sum(t.get("pnl", 0) for t in trade_log)
        
        # Circuit breaker stats
        breaker = get_breaker_status()
        daily_pnl = breaker.get("daily_pnl", 0)
        drawdown = breaker.get("drawdown_pct", 0)
        
        message = (
            f"💰 *Portfolio Equity*\n\n"
            f"📊 *Balances*\n"
            f"├ Cash: `₹{balance:,.2f}`\n"
            f"├ Invested: `₹{total_invested:,.2f}`\n"
            f"├ Position Value: `₹{total_current_value:,.2f}`\n"
            f"└ *Total Equity*: `₹{total_equity:,.2f}`\n\n"
            f"📈 *Performance*\n"
            f"├ Unrealized P&L: `₹{unrealized_pnl:,.2f}`\n"
            f"├ Realized P&L: `₹{realized_pnl:,.2f}`\n"
            f"├ Today's P&L: `₹{daily_pnl:,.2f}`\n"
            f"└ Drawdown: `{drawdown:.1f}%`\n\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Equity command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def safety_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: VGX Telegram Bot - V1
    Show safety systems status.
    """
    try:
        breaker = get_breaker_status()
        risk = _get_risk_status()
        
        # Kill switches
        trading_enabled = os.getenv("TRADING_ENABLED", "true").lower() == "true"
        emergency_stop = os.getenv("EMERGENCY_STOP", "false").lower() == "true"
        
        # Circuit breaker
        cb_state = breaker.get("trading_state", "UNKNOWN")
        can_trade = breaker.get("can_trade", False)
        daily_pnl_pct = breaker.get("daily_pnl_pct", 0)
        weekly_pnl_pct = breaker.get("weekly_pnl_pct", 0)
        drawdown_pct = breaker.get("drawdown_pct", 0)
        trades_blocked = breaker.get("total_trades_blocked", 0)
        
        # Risk engine
        market_regime = risk.get("market_regime", "UNKNOWN")
        cooldown_active = risk.get("cooldown", {}).get("active", False)
        loss_streak = risk.get("cooldown", {}).get("loss_streak", 0)
        
        # Status emojis
        kill_emoji = "🟢" if trading_enabled and not emergency_stop else "🔴"
        cb_emoji = "🟢" if cb_state == "ACTIVE" else "🔴"
        market_emoji = "🟢" if market_regime in ("BULL", "SIDEWAYS") else "🔴"
        
        message = (
            f"🛡️ *Safety Systems*\n\n"
            f"🔒 *Kill Switches*\n"
            f"├ {kill_emoji} Trading Enabled: `{trading_enabled}`\n"
            f"└ {'🔴' if emergency_stop else '🟢'} Emergency Stop: `{emergency_stop}`\n\n"
            f"⚡ *Circuit Breaker*\n"
            f"├ {cb_emoji} State: `{cb_state}`\n"
            f"├ Can Trade: `{can_trade}`\n"
            f"├ Daily P&L: `{daily_pnl_pct:+.2f}%`\n"
            f"├ Weekly P&L: `{weekly_pnl_pct:+.2f}%`\n"
            f"├ Drawdown: `{drawdown_pct:.2f}%`\n"
            f"└ Trades Blocked: `{trades_blocked}`\n\n"
            f"📊 *Risk Engine*\n"
            f"├ {market_emoji} Market: `{market_regime}`\n"
            f"├ Cooldown: `{cooldown_active}`\n"
            f"└ Loss Streak: `{loss_streak}`\n\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Safety command failed: %s", e)
        await update.message.reply_text(f"❌ Error: {str(e)}")


# ============================================================
# BOT INITIALIZATION
# ============================================================

def create_vgx_bot():
    """
    FIX: VGX Telegram Bot - V1
    Create and configure the VGX telegram bot.
    """
    if not VGX_BOT_TOKEN:
        logger.warning("VGX_BOT_TOKEN not set - VGX Telegram bot disabled")
        return None
    
    try:
        app = ApplicationBuilder().token(VGX_BOT_TOKEN).build()
        
        # Register command handlers
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        app.add_handler(CommandHandler("positions", positions_cmd))
        app.add_handler(CommandHandler("equity", equity_cmd))
        app.add_handler(CommandHandler("safety", safety_cmd))
        
        logger.info("VGX Telegram bot initialized")
        return app
        
    except Exception as e:
        logger.error("Failed to create VGX bot: %s", e)
        return None


async def run_vgx_bot():
    """
    FIX: VGX Telegram Bot - V1
    Run the VGX telegram bot.
    """
    app = create_vgx_bot()
    if app is None:
        return
    
    logger.info("Starting VGX Telegram bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    # Keep running
    while True:
        await asyncio.sleep(3600)


# ============================================================
# LIFESPAN HOOKS — used by app.py alongside scanner/mtb/pmb bots
# ============================================================

_VGX_TG_TASK = None
_VGX_TG_APP = None


async def startup_event() -> None:
    global _VGX_TG_TASK
    app = create_vgx_bot()
    if app is None:
        logger.warning("VGX Telegram bot not started — no token")
        return
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    globals()["_VGX_TG_APP"] = app
    logger.info("VGX Telegram bot started")


async def shutdown_event() -> None:
    app = globals().get("_VGX_TG_APP")
    if app is not None:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("VGX Telegram bot stopped")


# ============================================================
# STANDALONE ENTRY POINT
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    )
    
    asyncio.run(run_vgx_bot())
