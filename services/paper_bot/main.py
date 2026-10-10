"""
PROJECT-ALPHA — Alpha-Paper Main Runner (Simulated Execution Environment).

Standalone service for paper trade execution, position management, paper P&L,
paper portfolio tracking, and Paper Telegram C2 Bot.
Uses isolated database: paper/data/alpha_paper.db
Strictly rejects mode != PAPER.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
_SERVICES = _DIR.parent
_PROJECT_ROOT = _SERVICES.parent
_SHARED = _PROJECT_ROOT / "shared"

if str(_DIR) in sys.path:
    sys.path.remove(str(_DIR))
for _p in [_SHARED, _SERVICES, _PROJECT_ROOT]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from background.journal.service import JournalService
from background.portfolio.service import PortfolioService
from core.bus import bus
from core.bus.event_types import EventType
from core.config import get_config
from core.logging import get_logger
from core.repository.db import Database
from core.repository.journal_repo import JournalRepository
from core.repository.position_repo import PositionRepository
from core.repository.qpb_repo import QPBRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from execution.risk.capital_guard import CapitalGuard
from paper.config.settings import get_paper_config
from paper.execution.router import PaperExecutionRouter
from paper.portfolio import QPBPortfolioService
from paper.telegram.notifier import PaperTelegramNotifier

logger = get_logger("paper.main")


async def run_paper():
    logger.info("Initializing Alpha-Paper execution service...")
    p_cfg = get_paper_config()

    # Hard isolation: use paper/data/alpha_paper.db ONLY for writing paper execution
    paper_db_path = Path(p_cfg.db_path)
    if not paper_db_path.is_absolute():
        paper_db_path = (_PROJECT_ROOT / paper_db_path).resolve()
    db = Database(str(paper_db_path))
    await db.open()

    position_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    qpb_repo = QPBRepository(db.connection)
    journal_repo = JournalRepository(db.connection)

    journal_service = JournalService(bus=bus, journal_repo=journal_repo)
    await journal_service.start()

    app_config = get_config()
    app_config.deployment_mode = "PAPER"
    app_config.db_path = str(paper_db_path)

    portfolio_service = PortfolioService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        config=app_config,
    )
    await portfolio_service.start()

    position_manager = PositionManager(
        position_repo=position_repo,
        trade_repo=trade_repo,
        bus=bus,
    )

    auto_trader = AutoTradeRouter(
        bus=bus,
        dry_run=True,
        position_repo=position_repo,
    )

    capital_guard = CapitalGuard(app_config)
    paper_router = PaperExecutionRouter(
        bus=bus,
        auto_trader=auto_trader,
        position_manager=position_manager,
        capital_guard=capital_guard,
        position_repo=position_repo,
    )
    qpb_service = QPBPortfolioService(
        config=app_config,
        qpb_repo=qpb_repo,
        position_repo=position_repo,
        trade_repo=trade_repo,
        execution_router=paper_router,
    )

    # Read-only inspection of Scanner DB for signal intake & Telegram telemetry
    scanner_db_path = Path(p_cfg.scanner_db_path)
    if not scanner_db_path.is_absolute():
        scanner_db_path = (_PROJECT_ROOT / scanner_db_path).resolve()
    scanner_db: Database | None = None
    scanner_signal_repo: SignalRepository | None = None

    async def _init_scanner_db() -> bool:
        nonlocal scanner_db, scanner_signal_repo
        if scanner_signal_repo is not None and scanner_db is not None and scanner_db.is_open:
            return True

        if scanner_db is not None:
            try:
                await scanner_db.close()
            except Exception:
                pass
            scanner_db = None
            scanner_signal_repo = None

        if scanner_db_path.exists():
            try:
                scanner_db = Database(str(scanner_db_path), read_only=True)
                await scanner_db.open()
                scanner_signal_repo = SignalRepository(scanner_db.connection)
                if telegram_notifier:
                    telegram_notifier._signal_repo = scanner_signal_repo
                    if hasattr(telegram_notifier, "interface"):
                        telegram_notifier.interface.signal_repo = scanner_signal_repo
                logger.info("Alpha-Paper connected to Scanner DB (read-only): %s", scanner_db_path)
                return True
            except Exception as scan_db_err:
                logger.warning("Alpha-Paper could not connect to Scanner DB: %s", scan_db_err, exc_info=True)
                if scanner_db:
                    try:
                        await scanner_db.close()
                    except Exception:
                        pass
                scanner_db = None
                scanner_signal_repo = None
        else:
            logger.warning("Alpha-Paper: Scanner DB does not exist at %s", scanner_db_path)
        return False

    telegram_notifier = None
    await _init_scanner_db()

    telegram_notifier = PaperTelegramNotifier(
        bus=bus,
        config=app_config,
        position_repo=position_repo,
        trade_repo=trade_repo,
        portfolio_service=portfolio_service,
        execution_router=paper_router,
        signal_repo=scanner_signal_repo,
        qpb_service=qpb_service,
    )
    await telegram_notifier.start()

    # Subscribe paper execution handler to event bus
    bus.subscribe(EventType.SIGNAL_GENERATED, qpb_service.handle_signal_event)
    logger.info(
        "Alpha-Paper service RUNNING. Listening for signals (DB: %s).",
        p_cfg.db_path,
    )

    poll_lock = asyncio.Lock()

    async def poll_scanner_signals():
        nonlocal scanner_db, scanner_signal_repo
        if poll_lock.locked():
            return
        async with poll_lock:
            if not scanner_signal_repo or not scanner_db or not scanner_db.is_open:
                if not await _init_scanner_db():
                    return
            try:
                live_sigs = await scanner_signal_repo.get_live(grace_period_seconds=15.0)
                if not live_sigs:
                    return

                # Deduplicate: filter out signals that QPB or position_repo has already processed
                unclaimed = []
                for s in live_sigs:
                    sid = (
                        getattr(s, "id", None)
                        or (s.get("id") if isinstance(s, dict) else None)
                        or (s.get("signal_id") if isinstance(s, dict) else None)
                    )
                    if sid:
                        existing_decision = await qpb_repo.get(str(sid))
                        if existing_decision and existing_decision.get("status") in {
                            "EXECUTED",
                            "REJECTED",
                            "EXPIRED",
                            "DUPLICATE_SIGNAL",
                        }:
                            continue
                        existing_pos = await position_repo.get_by_signal_id(str(sid))
                        if existing_pos is not None:
                            continue
                        unclaimed.append(s)

                if unclaimed:
                    def _get_score(sig: Any) -> float:
                        if hasattr(sig, "score"):
                            return float(sig.score or 0.0)
                        if isinstance(sig, dict):
                            return float(sig.get("score") or 0.0)
                        return 0.0

                    # Sort descending by score to pick the highest conviction candidates first
                    unclaimed.sort(key=_get_score, reverse=True)
                    # Stagger entries: take up to 2 candidates, or up to 4 if backlog accumulated after disconnect
                    batch_size = 2 if len(unclaimed) <= 4 else min(4, len(unclaimed))
                    batch = unclaimed[:batch_size]
                    logger.info(
                        "Alpha-Paper QPB staggered evaluation: processing top %d of %d pending Scanner signals",
                        len(batch),
                        len(unclaimed),
                    )
                    await qpb_service.process_signals(batch)
            except Exception as poll_err:
                logger.error(
                    "Error polling scanner signals in Alpha-Paper: %s; resetting scanner connection",
                    poll_err,
                    exc_info=True,
                )
                if scanner_db:
                    try:
                        await scanner_db.close()
                    except Exception:
                        pass
                scanner_db = None
                scanner_signal_repo = None
                if telegram_notifier:
                    telegram_notifier._signal_repo = None
                    if hasattr(telegram_notifier, "interface"):
                        telegram_notifier.interface.signal_repo = None

    async def poll_mark_to_market_exits(price_override: dict[str, float] | None = None):
        """
        Polls live prices for active paper positions, updates mark prices and unrealized PnL,
        checks TP/SL triggers, and executes paper exits when targets are reached.
        """
        try:
            open_positions = await position_repo.get_active_positions(mode=BotMode.PAPER)
            if not open_positions:
                return

            current_prices: dict[str, float] = {}
            if price_override:
                current_prices = dict(price_override)
            else:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=4.0) as client:
                        resp = await client.get("https://api.coindcx.com/exchange/ticker")
                        if resp.status_code == 200:
                            for item in resp.json():
                                m = str(item.get("market", "")).upper()
                                try:
                                    raw_p = item.get("last_price")
                                    if raw_p is None:
                                        continue
                                    last_p = float(raw_p)
                                except (ValueError, TypeError):
                                    continue
                                if last_p > 0:
                                    current_prices[m] = last_p
                                    if m.startswith("B-") and "_" in m:
                                        base, quote = m[2:].split("_", 1)
                                        current_prices[f"{base}/{quote}"] = last_p
                                        current_prices[f"{base}{quote}"] = last_p
                                    elif m.endswith("INR") and len(m) > 3:
                                        coin = m[:-3]
                                        current_prices[f"{coin}/INR"] = last_p
                                        current_prices[f"{coin}INR"] = last_p
                                        current_prices[f"B-{coin}_INR"] = last_p
                                    elif m.endswith("USDT") and len(m) > 4:
                                        coin = m[:-4]
                                        current_prices[f"{coin}/USDT"] = last_p
                                        current_prices[f"{coin}USDT"] = last_p
                                        current_prices[f"B-{coin}_USDT"] = last_p
                except Exception as net_err:
                    logger.debug("Failed to fetch CoinDCX ticker for paper mark-to-market: %s", net_err)
                    return

            if not current_prices:
                return

            unique_pairs = {pos.pair for pos in open_positions}
            for pair in unique_pairs:
                target_pair = pair.upper().replace("_", "/")
                mark_px = current_prices.get(target_pair)
                if mark_px is None:
                    mark_px = current_prices.get(target_pair.replace("/", ""))
                if mark_px is None:
                    b_pair = f"B-{target_pair.replace('/', '_')}"
                    mark_px = current_prices.get(b_pair)

                if mark_px is None or mark_px <= 0:
                    continue

                triggers = await position_manager.evaluate_brackets(pair, mark_px)
                for trig_pos, exit_reason, exit_price in triggers:
                    logger.info(
                        "Alpha-Paper triggered EXIT: %s (%s) via %s @ %.4f",
                        trig_pos.id,
                        trig_pos.pair,
                        exit_reason.value if hasattr(exit_reason, "value") else str(exit_reason),
                        exit_price,
                    )
                    await position_manager.close_position(
                        position_id=trig_pos.id,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                    )
        except Exception as exit_err:
            logger.error("Error in paper mark-to-market exit polling: %s", exit_err, exc_info=True)

    try:
        while True:
            await poll_scanner_signals()
            await poll_mark_to_market_exits()
            await asyncio.sleep(15)
    except asyncio.CancelledError:
        logger.info("Alpha-Paper service shutting down gracefully...")
    finally:
        bus.unsubscribe(EventType.SIGNAL_GENERATED, qpb_service.handle_signal_event)
        await telegram_notifier.stop()
        await portfolio_service.stop()
        await journal_service.stop()
        if scanner_db:
            await scanner_db.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_paper())
    except KeyboardInterrupt:
        logger.info("Alpha-Paper process terminated by user.")
