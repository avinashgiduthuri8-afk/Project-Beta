"""
PROJECT-ALPHA Trading Bot (VGX)
Railway Production Main File

When run standalone (__main__): starts its own Telegram polling loop.
When embedded in app.py: background_loop() is started as an asyncio task by
startup_event() and cancelled by shutdown_event().
"""

import asyncio
import atexit
import logging
import os
from typing import Optional

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler
)

from .config import *
from . import storage

from .analytics import (
    stats_cmd,
    history_cmd,
    analytics_cmd,
    update_stats
)

from .telegram_handlers import (
    start_cmd,
    status_cmd,
    help_cmd,
    buy_cmd,
    sell_cmd,
    tradeamount_cmd,
    mode_cmd,
    setmode_cmd,
    threshold_cmd,
    setthreshold_cmd
)

from .market_data import (
    update_market_cache
)

from .alerts import auto_alerts

from .exit_engine import auto_sell

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("vgx_bot")

VGX_ENABLED = os.getenv("VGX_ENABLED", "true").lower() == "true"

# ── Embedded lifecycle (used when running inside app.py) ─────────────────────
_VGX_TASK: Optional[asyncio.Task] = None


def startup():
    logger.info("================================")
    logger.info("PROJECT-ALPHA STARTING")
    logger.info("Loading Storage...")
    storage.load_data()
    logger.info("Balance : ₹%s", storage.virtual_balance)
    logger.info("Startup Complete")
    logger.info("================================")


async def background_loop():
    if not VGX_ENABLED:
        logger.info("VGX background loop DISABLED (set VGX_ENABLED=true to activate)")
        return
    logger.info("VGX background loop started interval=%ss", STORAGE_SYNC_INTERVAL)
    logger.info("Background Engine Started")
    while True:
        try:
            # Offload only the network-bound market-data fetch so the event loop
            # is not blocked during the HTTP call.  All state-mutating operations
            # (auto_sell, update_stats, save_data) stay on the event-loop thread
            # so they execute sequentially and never race against each other.
            await asyncio.to_thread(update_market_cache)
            try:
                from . import scanner_bridge as _sb
                for _sig in _sb.get_signals():
                    _sb.process_scanner_signal(_sig)
            except Exception as _e:
                logger.warning("VGX scanner bridge step failed: %s", _e)
            await auto_alerts()
            auto_sell()
            update_stats()
            storage.save_data()
        except Exception as e:
            logger.error("[BACKGROUND ERROR] %s", e)
        await asyncio.sleep(STORAGE_SYNC_INTERVAL)


async def startup_event() -> None:
    """Idempotent startup for embedded use in app.py lifespan."""
    global _VGX_TASK
    startup()
    logger.info("VGX Bot starting (enabled=%s)", VGX_ENABLED)
    logger.info("VGX BOT_MODE=%s", os.getenv("VGX_BOT_MODE", "PAPER"))
    if _VGX_TASK is None or _VGX_TASK.done():
        _VGX_TASK = asyncio.create_task(background_loop())
        logger.info("VGX background task created")


async def shutdown_event() -> None:
    """Graceful shutdown for embedded use in app.py lifespan."""
    global _VGX_TASK
    if _VGX_TASK is not None and not _VGX_TASK.done():
        _VGX_TASK.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_VGX_TASK), timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("VGX background loop stopped")
    storage.save_data()
    logger.info("VGX storage saved on shutdown")


# ── Standalone lifecycle (used when run as __main__) ─────────────────────────

async def post_init(app):
    asyncio.create_task(background_loop())


def main():
    if not BOT_TOKEN:
        logger.warning("[VGX] BOT_TOKEN not set — Telegram bot disabled")
        logger.warning("[VGX] Running headless (scanner + trading engine only)")
        return

    startup()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("tradeamount", tradeamount_cmd))
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("setmode", setmode_cmd))
    app.add_handler(CommandHandler("threshold", threshold_cmd))
    app.add_handler(CommandHandler("setthreshold", setthreshold_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("analytics", analytics_cmd))

    atexit.register(storage.save_data)

    logger.info("🚀 PROJECT-ALPHA LIVE")
    app.run_polling()


if __name__ == "__main__":
    main()
