"""
PROJECT-ALPHA MTB Bot entrypoint.

MTB is Trading Bot 2. It consumes scanner signals, executes fixed-size paper
BUYs, and exposes Telegram controls.

When run standalone (__main__): starts its own Telegram polling loop.
When embedded in app.py: background_loop() is started as an asyncio task by
startup_event() and cancelled by shutdown_event().
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from telegram.ext import ApplicationBuilder, CommandHandler

from . import storage
from .config import POLL_INTERVAL_SECONDS, TELEGRAM_BOT_TOKEN
from .telegram_handlers import (
    buy_cmd,
    sell_cmd,
    start_cmd,
    status_cmd,
    tradeamount_cmd,
)
from .trading_engine import run_cycle

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mtb_bot")

MTB_ENABLED: bool = os.getenv("MTB_ENABLED", "false").lower() == "true"

# ── Embedded lifecycle (used when running inside app.py) ─────────────────────
_MTB_TASK: Optional[asyncio.Task] = None


async def background_loop() -> None:
    if not MTB_ENABLED:
        logger.info("MTB background loop DISABLED (set MTB_ENABLED=true to activate)")
        return
    logger.info("MTB background loop started interval=%ss", POLL_INTERVAL_SECONDS)
    while True:
        try:
            summary = await run_cycle()
            logger.info("MTB cycle complete: %s", summary)
        except Exception:
            logger.exception("MTB background loop failed; retrying")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def startup_event() -> None:
    """Idempotent startup for embedded use in app.py lifespan."""
    global _MTB_TASK
    storage.ensure_storage()
    logger.info("MTB Bot starting (enabled=%s)", MTB_ENABLED)
    logger.info("MTB BOT_MODE=%s", os.getenv("MTB_BOT_MODE", "PAPER"))
    if _MTB_TASK is None or _MTB_TASK.done():
        _MTB_TASK = asyncio.create_task(background_loop())
        logger.info("MTB background task created")


async def shutdown_event() -> None:
    """Graceful shutdown for embedded use in app.py lifespan."""
    global _MTB_TASK
    if _MTB_TASK is not None and not _MTB_TASK.done():
        _MTB_TASK.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_MTB_TASK), timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("MTB background loop stopped")


# ── Standalone lifecycle (used when run as __main__) ─────────────────────────

async def post_init(app) -> None:
    app.create_task(background_loop())


def build_application():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("MTB_BOT_TOKEN or BOT_TOKEN must be set.")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("tradeamount", tradeamount_cmd))
    return app


def main() -> None:
    storage.ensure_storage()
    logger.info("MTB Bot starting")
    build_application().run_polling()


if __name__ == "__main__":
    main()
