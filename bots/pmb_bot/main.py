"""
PROJECT-ALPHA PMB Bot entrypoint.

PMB = Price Movement Bot.
Runs a background cycle loop and exposes Telegram controls.
Starts DISABLED by default — set PMB_ENABLED=true to activate the cycle loop.

When run standalone (__main__): starts its own Telegram polling loop.
When embedded in app.py: background_loop() is started as an asyncio task by
startup_event() and cancelled by shutdown_event().
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from . import storage
from .config import POLL_INTERVAL_SECONDS, TELEGRAM_BOT_TOKEN
from .trading_engine import run_cycle

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("pmb_bot")

PMB_ENABLED = os.getenv("PMB_ENABLED", "false").lower() == "true"

# ── Embedded lifecycle (used when running inside app.py) ─────────────────────
_PMB_TASK: Optional[asyncio.Task] = None


async def background_loop() -> None:
    if not PMB_ENABLED:
        logger.info("PMB background loop DISABLED (set PMB_ENABLED=true to activate)")
        return
    logger.info("PMB background loop started  interval=%ss", POLL_INTERVAL_SECONDS)
    while True:
        try:
            summary = await run_cycle()
            logger.info("PMB cycle: %s", summary)
        except Exception:
            logger.exception("PMB background loop error; retrying")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def startup_event() -> None:
    """Idempotent startup for embedded use in app.py lifespan."""
    global _PMB_TASK
    storage.ensure_storage()
    logger.info("PMB Bot starting (enabled=%s)", PMB_ENABLED)
    logger.info("PMB BOT_MODE=%s", os.getenv("PMB_BOT_MODE", "PAPER"))
    if _PMB_TASK is None or _PMB_TASK.done():
        _PMB_TASK = asyncio.create_task(background_loop())
        logger.info("PMB background task created")


async def shutdown_event() -> None:
    """Graceful shutdown for embedded use in app.py lifespan."""
    global _PMB_TASK
    if _PMB_TASK is not None and not _PMB_TASK.done():
        _PMB_TASK.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_PMB_TASK), timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("PMB background loop stopped")


# ── Standalone lifecycle (used when run as __main__) ─────────────────────────

async def post_init(app) -> None:
    app.create_task(background_loop())


def build_application():
    from telegram.ext import ApplicationBuilder, CommandHandler
    from .telegram_handlers import (
        buy_cmd, sell_cmd, start_cmd, status_cmd,
    )
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("PMB_BOT_TOKEN or BOT_TOKEN must be set.")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",     start_cmd))
    app.add_handler(CommandHandler("status",    status_cmd))
    app.add_handler(CommandHandler("buy",       buy_cmd))
    app.add_handler(CommandHandler("sell",      sell_cmd))
    return app


def main() -> None:
    storage.ensure_storage()
    logger.info("PMB Bot starting (enabled=%s)", PMB_ENABLED)
    build_application().run_polling()


if __name__ == "__main__":
    main()
