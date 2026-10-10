"""
PROJECT-ALPHA — Alpha-Scanner Main Runner (Brain).

Standalone service for market intelligence, scanning, indicator analysis, coin ranking,
and signal dispatching. Does NOT execute trades.
"""

import asyncio
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_SERVICES = _DIR.parent
_PROJECT_ROOT = _SERVICES.parent
_SHARED = _PROJECT_ROOT / "shared"

if str(_DIR) in sys.path:
    sys.path.remove(str(_DIR))
for _p in [_SHARED, _SERVICES, _PROJECT_ROOT]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.bus import bus
from core.config import get_config
from core.logging import get_logger
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.signal_repo import SignalRepository
from scanner.config.settings import get_scanner_config
from scanner.service import ScannerService
from scanner.telegram.notifier import ScannerTelegramNotifier

logger = get_logger("scanner.main")


async def run_scanner():
    logger.info("Initializing Alpha-Scanner (Brain) service...")
    s_cfg = get_scanner_config()
    scanner_db_path = Path(s_cfg.db_path)
    if not scanner_db_path.is_absolute():
        scanner_db_path = (_PROJECT_ROOT / scanner_db_path).resolve()
    db = Database(str(scanner_db_path))
    await db.open()

    signal_repo = SignalRepository(db.connection)
    event_log_repo = EventLogRepository(db.connection)
    candle_repo = CandleRepository(db.connection)
    app_config = get_config()
    learner_db_path = Path(s_cfg.learner_db_path)
    if not learner_db_path.is_absolute():
        learner_db_path = (_PROJECT_ROOT / learner_db_path).resolve()

    scanner_service = ScannerService(
        bus=bus,
        signal_repo=signal_repo,
        event_log_repo=event_log_repo,
        config=app_config,
        candle_repo=candle_repo,
        learner_db_path=str(learner_db_path),
    )
    await scanner_service.start()

    telegram_notifier = ScannerTelegramNotifier(
        bus=bus,
        config=app_config,
        signal_repo=signal_repo,
        scanner_service=scanner_service,
        event_log_repo=event_log_repo,
    )
    await telegram_notifier.start()

    logger.info(
        "Alpha-Scanner service RUNNING. Listening for market cycles (poll_interval=%ds).",
        s_cfg.poll_interval,
    )

    try:
        while True:
            await scanner_service.poll()
            await asyncio.sleep(s_cfg.poll_interval)
    except asyncio.CancelledError:
        logger.info("Alpha-Scanner service shutting down gracefully...")
    finally:
        await telegram_notifier.stop()
        await scanner_service.stop()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_scanner())
    except KeyboardInterrupt:
        logger.info("Alpha-Scanner process terminated by user.")
