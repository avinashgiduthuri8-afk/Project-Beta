"""
PROJECT-ALPHA — Alpha-Live Main Runner (Real Money Execution Environment).

Standalone service for live trade execution, position management, live P&L,
live portfolio tracking, and Live Telegram C2 Bot.
Uses isolated database: live/data/alpha_live.db
Strictly rejects mode != LIVE.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DIR = Path(__file__).resolve().parent
if str(_DIR) in sys.path:
    sys.path.remove(str(_DIR))
if str(_ROOT) in sys.path:
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from background.portfolio.service import PortfolioService
from core.bus import bus
from core.bus.event_types import EventType
from core.config import get_config
from core.logging import get_logger
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.order_repo import OrderRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, Signal
from execution.auto_trader import AutoTradeRouter
from execution.trading.broker_manager import IndianBrokerManager
from live.config.settings import get_live_config
from live.execution.router import LiveExecutionRouter
from live.telegram.notifier import LiveTelegramNotifier

logger = get_logger("live.main")



def live_signal_payload(signal: Signal | dict[str, Any]) -> dict[str, Any]:
    if isinstance(signal, dict):
        p = dict(signal)
        p["mode"] = BotMode.LIVE.value
        return p
    raw = dict(signal.raw_payload or {})
    payload = dict(raw)
    payload.update(
        {
            "id": signal.id,
            "signal_id": signal.id,
            "mode": BotMode.LIVE.value,
            "coin": signal.coin,
            "symbol": signal.coin,
            "pair": signal.pair,
            "strategy": signal.source_bot or raw.get("strategy") or raw.get("bot") or "STE",
            "bot": signal.source_bot or raw.get("bot") or raw.get("strategy") or "STE",
            "strategy_name": raw.get("strategy_name"),
            "entry_price": raw.get("entry_price", raw.get("price")),
            "price": raw.get("price", raw.get("entry_price")),
            "take_profit": raw.get("take_profit"),
            "stop_loss": raw.get("stop_loss"),
            "score": signal.score,
            "confidence": signal.confidence,
            "indicators": raw.get("indicators", {}),
            "signal_reason": raw.get("signal_reason", ""),
            "generated_at": (
                signal.generated_at.isoformat()
                if hasattr(signal.generated_at, "isoformat")
                else str(signal.generated_at)
            ),
            "timestamp": (
                signal.generated_at.isoformat()
                if hasattr(signal.generated_at, "isoformat")
                else str(signal.generated_at)
            ),
            "expires_at": (
                signal.expires_at.isoformat()
                if hasattr(signal.expires_at, "isoformat")
                else str(signal.expires_at)
            ),
            "opportunity_type": (
                signal.opportunity_type.value
                if hasattr(signal.opportunity_type, "value")
                else str(signal.opportunity_type)
            ),
            "market_state": (
                signal.market_state.value
                if hasattr(signal.market_state, "value")
                else str(signal.market_state)
            ),
        }
    )
    return payload

async def run_live():
    logger.info("Initializing Alpha-Live execution service...")
    l_cfg = get_live_config()

    # Hard isolation: use live/data/alpha_live.db ONLY
    db = Database(l_cfg.db_path)
    await db.open()

    position_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    _event_log_repo = EventLogRepository(db.connection)
    _order_repo = OrderRepository(db.connection)

    app_config = get_config()
    app_config.deployment_mode = "LIVE"
    app_config.db_path = l_cfg.db_path

    portfolio_service = PortfolioService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        config=app_config,
    )
    await portfolio_service.start()

    auto_trader = AutoTradeRouter(
        bus=bus,
        dry_run=not l_cfg.trading_enabled,
        position_repo=position_repo,
        subaccount_manager=IndianBrokerManager(app_config),
    )

    live_router = LiveExecutionRouter(
        bus=bus,
        auto_trader=auto_trader,
    )

    # Read-only inspection of Scanner DB for Live Telegram telemetry
    scanner_db_path = Path("scanner/data/alpha_scanner.db")
    scanner_db = None
    scanner_signal_repo = None
    if scanner_db_path.exists():
        try:
            scanner_db = Database(str(scanner_db_path))
            await scanner_db.open()
            scanner_signal_repo = SignalRepository(scanner_db.connection)
            logger.info("Alpha-Live connected to Scanner DB (read-only): %s", scanner_db_path)
        except Exception as scan_db_err:
            logger.warning("Alpha-Live could not connect to Scanner DB: %s", scan_db_err)

    telegram_notifier = LiveTelegramNotifier(
        bus=bus,
        config=app_config,
        position_repo=position_repo,
        trade_repo=trade_repo,
        portfolio_service=portfolio_service,
        execution_router=live_router,
        signal_repo=scanner_signal_repo,
    )
    await telegram_notifier.start()

    # Subscribe live execution handler to event bus
    bus.subscribe(EventType.SIGNAL_GENERATED, live_router.handle_signal_event)
    logger.info(
        "Alpha-Live service RUNNING (Trading Enabled: %s, DB: %s).",
        l_cfg.trading_enabled,
        l_cfg.db_path,
    )

    async def poll_scanner_signals():
        if not scanner_signal_repo:
            return
        try:
            live_sigs = await scanner_signal_repo.get_live()
            if live_sigs:
                logger.info("Alpha-Live evaluating %d Scanner-qualified signals", len(live_sigs))
                for sig in live_sigs:
                    payload = live_signal_payload(sig)
                    await live_router.handle_signal_event(EventType.SIGNAL_GENERATED, payload)
        except Exception as poll_err:
            logger.error("Error polling scanner signals in Alpha-Live: %s", poll_err)

    try:
        while True:
            await poll_scanner_signals()
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("Alpha-Live service shutting down gracefully...")
    finally:
        bus.unsubscribe(EventType.SIGNAL_GENERATED, live_router.handle_signal_event)
        await telegram_notifier.stop()
        await portfolio_service.stop()
        if scanner_db:
            await scanner_db.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_live())
    except KeyboardInterrupt:
        logger.info("Alpha-Live process terminated by user.")
