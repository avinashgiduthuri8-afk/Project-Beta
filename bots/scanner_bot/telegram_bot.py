"""
PROJECT-ALPHA Scanner Telegram Bot
FIX: Scanner Telegram Bot - V1

Commands:
- /status - Scanner operational status
- /signals - Current active signals
- /health - Scanner health metrics
- /refresh - Force scanner refresh
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

# Scanner data imports
from bots.scanner_bot.scanner import (
    get_signals,
    get_live_signals,
    get_stats,
    get_watchlist,
    get_market_state,
    get_signal_stats,
)

# BUGFIX (V1 status source): /status now reads the scanner's live in-memory
# runtime state (same source app.py's dashboard already uses) instead of
# stats.json, which never stores total_scans / last_scan_time.
import bots.scanner_bot.main as scanner_main

logger = logging.getLogger("scanner_telegram_bot")

# ============================================================
# CONFIGURATION
# ============================================================

# Multi-bot configuration - V1
# ENV: SCANNER_BOT_TOKEN - Telegram bot token for Scanner (fallback: BOT_TOKEN)
# ENV: SCANNER_CHAT_ID - Chat ID for Scanner notifications
SCANNER_BOT_TOKEN = os.getenv("SCANNER_BOT_TOKEN", os.getenv("BOT_TOKEN", ""))
SCANNER_CHAT_ID = os.getenv("SCANNER_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))

# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: Scanner Telegram Bot - V1"""
    await update.message.reply_text(
        "🔍 *PROJECT-ALPHA Scanner Bot*\n\n"
        "Commands:\n"
        "/status - Scanner operational status\n"
        "/signals - Current active signals\n"
        "/health - Scanner health metrics\n"
        "/refresh - Force scanner refresh\n"
        "/help - Show this help",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FIX: Scanner Telegram Bot - V1"""
    await start_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: Scanner Telegram Bot - V1
    Show scanner operational status.
    """
    try:
        signals = get_signals() or {}
        live_signals = get_live_signals() or {}
        watchlist = get_watchlist() or {}

        # BUGFIX (V1 status source): total_scans / last_scan_time / scanner
        # running-state now come from the scanner's live in-memory runtime
        # state (_HEALTH_STATS / _SCANNER_TASK in bots/scanner_bot/main.py) —
        # the same source app.py's web dashboard already uses — instead of
        # stats.json, which never stores these two fields. Read defensively
        # via getattr since this module is a snapshot of shared state, not a
        # new counter, per the "reuse existing runtime state" requirement.
        health_stats = getattr(scanner_main, "_HEALTH_STATS", {}) or {}
        scanner_task = getattr(scanner_main, "_SCANNER_TASK", None)
        scanner_running = scanner_task is not None and not scanner_task.done()

        # Determine status — unwrap wrapped dict {"signals": [...]}
        total_signals = len(signals.get("signals", []))
        live_count = len(live_signals.get("signals", []))
        coins_watching = len(watchlist.get("coins", []))
        last_scan = health_stats.get("last_successful_scan") or "Unknown"
        total_scans = health_stats.get("total_scans", 0)

        # Operational status — running loop AND at least one completed scan
        is_healthy = scanner_running and total_scans > 0
        status_emoji = "🟢" if is_healthy else "🔴"
        status_text = "ONLINE" if is_healthy else "OFFLINE"
        
        message = (
            f"{status_emoji} *Scanner Status: {status_text}*\n\n"
            f"📊 *Statistics*\n"
            f"├ Total Scans: `{total_scans}`\n"
            f"├ Last Scan: `{last_scan}`\n"
            f"├ Coins Watching: `{coins_watching}`\n"
            f"└ Active Signals: `{total_signals}`\n\n"
            f"📡 *Live Signals*: `{live_count}`\n"
            f"⏱ Updated: `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Status command failed: %s", e)
        await update.message.reply_text(f"❌ Error fetching status: {str(e)}")


async def signals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: Scanner Telegram Bot - V1
    Show current active signals.
    """
    try:
        signals = (get_signals() or {}).get("signals", [])
        live_signals = (get_live_signals() or {}).get("signals", [])

        if not signals and not live_signals:
            await update.message.reply_text("📭 No active signals at the moment.")
            return
        
        # Combine and dedupe
        all_signals = {s.get("coin"): s for s in signals}
        for ls in live_signals:
            coin = ls.get("coin")
            if coin and coin not in all_signals:
                all_signals[coin] = ls
        
        # Sort by score descending
        sorted_signals = sorted(
            all_signals.values(),
            key=lambda x: x.get("score", 0),
            reverse=True
        )[:15]  # Limit to 15
        
        # Format message
        message = "📡 *Active Signals*\n\n"
        
        for i, sig in enumerate(sorted_signals, 1):
            coin = sig.get("coin", "???")
            score = sig.get("score", 0)
            tier = sig.get("tier", "?")
            action = sig.get("action", "?")
            
            # Tier emoji
            if tier in ("ELITE", "Premium", "PREMIUM"):
                tier_emoji = "🏆"
            elif tier in ("HIGH", "High", "Strong"):
                tier_emoji = "⭐"
            else:
                tier_emoji = "📊"
            
            message += f"{tier_emoji} `{coin}` | Score: `{score}` | {tier}\n"
        
        message += f"\n_Total: {len(all_signals)} signals_"
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Signals command failed: %s", e)
        await update.message.reply_text(f"❌ Error fetching signals: {str(e)}")


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: Scanner Telegram Bot - V1
    Show scanner health metrics.
    """
    try:
        stats = get_stats() or {}
        signal_stats = get_signal_stats() or {}
        market_state = get_market_state() or {}
        
        # Health metrics
        total_scans = stats.get("total_scans", 0)
        successful_scans = stats.get("successful_scans", total_scans)
        failed_scans = stats.get("failed_scans", 0)
        
        success_rate = (successful_scans / total_scans * 100) if total_scans > 0 else 0
        
        # Market state
        market_coins = len(market_state) if isinstance(market_state, dict) else 0
        
        # Signal breakdown
        elite_count = signal_stats.get("elite_signals", 0)
        high_count = signal_stats.get("high_signals", 0)
        medium_count = signal_stats.get("medium_signals", 0)
        
        # Health score
        health_score = min(100, int(success_rate))
        if health_score >= 90:
            health_emoji = "🟢"
            health_status = "EXCELLENT"
        elif health_score >= 70:
            health_emoji = "🟡"
            health_status = "GOOD"
        elif health_score >= 50:
            health_emoji = "🟠"
            health_status = "DEGRADED"
        else:
            health_emoji = "🔴"
            health_status = "CRITICAL"
        
        message = (
            f"{health_emoji} *Scanner Health: {health_status}*\n\n"
            f"📈 *Performance*\n"
            f"├ Health Score: `{health_score}%`\n"
            f"├ Total Scans: `{total_scans}`\n"
            f"├ Success Rate: `{success_rate:.1f}%`\n"
            f"└ Failed Scans: `{failed_scans}`\n\n"
            f"📊 *Signal Breakdown*\n"
            f"├ 🏆 Elite: `{elite_count}`\n"
            f"├ ⭐ High: `{high_count}`\n"
            f"└ 📊 Medium: `{medium_count}`\n\n"
            f"🌐 Market Coverage: `{market_coins}` coins\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error("Health command failed: %s", e)
        await update.message.reply_text(f"❌ Error fetching health: {str(e)}")


async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: Scanner Telegram Bot - V1
    Force scanner refresh.
    """
    try:
        await update.message.reply_text("🔄 Refreshing scanner data...")

        signals = (get_signals() or {}).get("signals", [])
        stats = get_stats() or {}
        signal_count = len(signals)
        
        await update.message.reply_text(
            f"✅ *Scanner Refreshed*\n\n"
            f"📡 Signals: `{signal_count}`\n"
            f"⏱ `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error("Refresh command failed: %s", e)
        await update.message.reply_text(f"❌ Refresh failed: {str(e)}")


# ============================================================
# BOT INITIALIZATION
# ============================================================

def create_scanner_bot():
    """
    FIX: Scanner Telegram Bot - V1
    Create and configure the scanner telegram bot.
    """
    if not SCANNER_BOT_TOKEN:
        logger.warning("SCANNER_BOT_TOKEN not set - Scanner Telegram bot disabled")
        return None
    
    try:
        app = ApplicationBuilder().token(SCANNER_BOT_TOKEN).build()
        
        # Register command handlers
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        app.add_handler(CommandHandler("signals", signals_cmd))
        app.add_handler(CommandHandler("health", health_cmd))
        app.add_handler(CommandHandler("refresh", refresh_cmd))
        
        logger.info("Scanner Telegram bot initialized")
        return app
        
    except Exception as e:
        logger.error("Failed to create scanner bot: %s", e)
        return None


async def run_scanner_bot():
    """
    FIX: Scanner Telegram Bot - V1
    Run the scanner telegram bot.
    """
    app = create_scanner_bot()
    if app is None:
        return
    
    logger.info("Starting Scanner Telegram bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    # Keep running
    while True:
        await asyncio.sleep(3600)


# ============================================================
# LIFESPAN HOOKS — used by app.py alongside vgx/mtb/pmb bots
# ============================================================

_SCANNER_TG_TASK = None
_SCANNER_TG_APP = None


async def startup_event() -> None:
    global _SCANNER_TG_TASK
    app = create_scanner_bot()
    if app is None:
        logger.warning("Scanner Telegram bot not started — no token")
        return
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    globals()["_SCANNER_TG_APP"] = app
    logger.info("Scanner Telegram bot started")


async def shutdown_event() -> None:
    app = globals().get("_SCANNER_TG_APP")
    if app is not None:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Scanner Telegram bot stopped")


# ============================================================
# STANDALONE ENTRY POINT
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    )
    
    asyncio.run(run_scanner_bot())
