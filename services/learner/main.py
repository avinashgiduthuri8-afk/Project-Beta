"""
PROJECT-ALPHA — Alpha-Learner Main Runner (Background Intelligence & Canary Calibration).

Standalone service coordinating:
  Stage 8: Mistake Pattern Identification Engine (Post-Trade Analysis)
  Stage 9: Walk-Forward Pre-Validation Backtesting Gate
  Stage 10: Dynamic Parameter Calibrator & Automated Canary Rollback

Supports both continuous daemon loop and single-pass batch execution (`--once`).
"""

from __future__ import annotations

import argparse
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
from core.logging import get_logger
from core.repository.backtest_repo import BacktestRepository
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from core.repository.feedback_repo import FeedbackRepository
from core.repository.journal_repo import JournalRepository
from core.repository.learning_repo import LearningRepository

from learner.config import get_learner_config
from learner.service import LearnerService
from learner.telegram.notifier import LearnerTelegramNotifier

logger = get_logger("learner.main")


async def run_learner(once: bool = False, interval: int | None = None) -> None:
    logger.info("Initializing Alpha-Learner service...")
    cfg = get_learner_config()
    if interval is not None and interval > 0:
        cfg.poll_interval_seconds = interval

    # 1. Resolve Learner DB
    learner_db_path = Path(cfg.db_path)
    if not learner_db_path.is_absolute():
        learner_db_path = (_PROJECT_ROOT / learner_db_path).resolve()
    learner_db = Database(str(learner_db_path))
    await learner_db.open()

    # 2. Resolve Paper DB (read-only connection to read trade_journal)
    paper_db_path = Path(cfg.paper_db_path)
    if not paper_db_path.is_absolute():
        paper_db_path = (_PROJECT_ROOT / paper_db_path).resolve()

    paper_db: Database | None = None
    if paper_db_path.exists():
        paper_db = Database(str(paper_db_path), read_only=True)
        await paper_db.open()
        paper_journal_repo = JournalRepository(paper_db.connection)
        logger.info("Alpha-Learner connected to Paper DB (read-only): %s", paper_db_path)
    else:
        # Fallback to learner_db if paper_db does not exist yet (e.g. fresh installation)
        logger.warning(
            "Paper DB not found at %s. Using Learner DB as trade_journal fallback.",
            paper_db_path,
        )
        paper_journal_repo = JournalRepository(learner_db.connection)

    # 3. Scanner candle history is read-only validation evidence for Stage 9.
    scanner_db_path = Path(cfg.scanner_db_path)
    if not scanner_db_path.is_absolute():
        scanner_db_path = (_PROJECT_ROOT / scanner_db_path).resolve()
    scanner_db: Database | None = None
    candle_repo: CandleRepository | None = None
    if scanner_db_path.exists():
        scanner_db = Database(str(scanner_db_path), read_only=True)
        await scanner_db.open()
        candle_repo = CandleRepository(scanner_db.connection)
        logger.info("Alpha-Learner connected to Scanner DB (read-only): %s", scanner_db_path)
    else:
        logger.warning(
            "Scanner DB not found at %s. Calibrations will fail closed until candle evidence is available.",
            scanner_db_path,
        )

    # 4. Instantiate Repositories
    learning_repo = LearningRepository(learner_db.connection)
    feedback_repo = FeedbackRepository(learner_db.connection)
    backtest_repo = BacktestRepository(learner_db.connection)

    # 5. Initialize LearnerService
    learner_service = LearnerService(
        config=cfg,
        journal_repo=paper_journal_repo,
        learning_repo=learning_repo,
        feedback_repo=feedback_repo,
        backtest_repo=backtest_repo,
        candle_repo=candle_repo,
        bus=bus,
    )

    if once:
        logger.info("Running single learning pass (--once)...")
        report = await learner_service.run_cycle()
        logger.info("Pass complete. Summary: %s", report)
        if paper_db:
            await paper_db.close()
        if scanner_db:
            await scanner_db.close()
        await learner_db.close()
        return

    telegram_notifier = LearnerTelegramNotifier(
        bus=bus,
        config=cfg,
        learner_service=learner_service,
    )
    await telegram_notifier.start()

    await learner_service.start()
    logger.info(
        "Alpha-Learner service RUNNING (interval=%ds).",
        cfg.poll_interval_seconds,
    )

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Alpha-Learner service shutting down gracefully...")
    finally:
        await telegram_notifier.stop()
        await learner_service.stop()
        if paper_db:
            await paper_db.close()
        if scanner_db:
            await scanner_db.close()
        await learner_db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha-Learner Service Runner")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single learning & calibration cycle, then exit.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Override cycle interval in seconds (default: 3600).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_learner(once=args.once, interval=args.interval))
    except KeyboardInterrupt:
        logger.info("Alpha-Learner process terminated by user.")


if __name__ == "__main__":
    main()
