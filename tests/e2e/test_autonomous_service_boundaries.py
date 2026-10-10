from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from background.backtest.service.service import BacktestService
from background.feedback.orchestrator import FeedbackOrchestrator
from background.journal.service import JournalService
from core.bus.event_bus import EventBus
from core.repository.backtest_repo import BacktestRepository
from core.repository.db import Database
from core.repository.feedback_repo import FeedbackRepository
from core.repository.journal_repo import JournalRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason
from execution.position_manager import PositionManager
from scanner.calibration_worker import CalibrationWorker
from scanner.confluence_engine import ConfluenceEngine


def _profitable_candles(count: int = 160) -> list[dict]:
    candles = []
    for i in range(count):
        phase = i % 8
        close = 102.0 if phase in (5, 6) else (107.0 if phase == 7 else 100.0)
        open_price = 102.0 if phase == 6 else close
        candles.append(
            {
                "timestamp": 1_700_000_000_000 + i * 3_600_000,
                "open": open_price,
                "high": 107.0 if phase == 7 else close + 0.3,
                "low": min(open_price, close) - 0.2,
                "close": close,
                "volume": 1000.0 + phase * 10,
            }
        )
    return candles


@pytest.mark.asyncio
async def test_learner_calibration_is_visible_to_scanner_read_only_connection(tmp_path):
    path = tmp_path / "learner.db"
    writer = Database(str(path))
    await writer.open()
    writer_repo = FeedbackRepository(writer.connection)
    await writer_repo.upsert_active_calibration("STE", 0.8, 90.0)

    reader = Database(str(path), read_only=True)
    await reader.open()
    engine = ConfluenceEngine()
    worker = CalibrationWorker(
        confluence_engine=engine,
        feedback_repo=FeedbackRepository(reader.connection),
    )
    await worker.run_calibration_cycle()

    assert engine.strategy_weights["STE"] == pytest.approx(0.8)
    assert engine.strategy_thresholds["STE"] == pytest.approx(90.0)

    await reader.close()
    await writer.close()


@pytest.mark.asyncio
async def test_canary_rollback_survives_restart_and_deduplicates_trades(tmp_path):
    db = Database(str(tmp_path / "learner.db"))
    await db.open()
    repo = FeedbackRepository(db.connection)
    backtests = BacktestService(BacktestRepository(db.connection))

    first = FeedbackOrchestrator(repo, backtests)
    promoted = await first.evaluate_and_validate_calibration(
        "STE", "BTC/INR", 1.2, 88.0, _profitable_candles()
    )
    assert promoted["status"] == "PROMOTED"
    after_promotion = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()

    assert await first.register_trade_outcome(
        "STE", "BTC/INR", False, "trade-1", after_promotion
    ) is None

    unchanged = await first.evaluate_and_validate_calibration(
        "STE", "BTC/INR", 1.2, 88.0, _profitable_candles()
    )
    assert unchanged["status"] == "UNCHANGED"

    restarted = FeedbackOrchestrator(repo, backtests)
    assert await restarted.register_trade_outcome(
        "STE", "BTC/INR", False, "trade-1", after_promotion
    ) is None
    rollback = await restarted.register_trade_outcome(
        "STE", "BTC/INR", False, "trade-2", after_promotion
    )
    assert rollback is not None
    assert rollback["status"] == "ROLLED_BACK"

    active = await repo.get_active_calibration("STE")
    assert active["weight_multiplier"] == pytest.approx(1.0)
    assert active["strict_threshold"] == pytest.approx(85.0)
    await db.close()


@pytest.mark.asyncio
async def test_journal_receives_durable_duration_excursions_and_regime(tmp_path):
    db = Database(str(tmp_path / "paper.db"))
    await db.open()
    bus = EventBus()
    positions = PositionRepository(db.connection)
    trades = TradeRepository(db.connection)
    journals = JournalRepository(db.connection)
    journal_service = JournalService(bus, journals)
    manager = PositionManager(positions, trades, bus)
    await journal_service.start()

    pos = await manager.open_position(
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=100.0,
        qty=2.0,
        stop_loss=90.0,
        take_profit=120.0,
        mode=BotMode.PAPER,
        market_regime="RISK_OFF",
    )
    opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.connection.execute(
        "UPDATE positions SET entry_time=? WHERE id=?",
        (opened_at.isoformat(), pos.id),
    )
    await db.connection.commit()

    persisted = await positions.get_by_id(pos.id)
    await manager.update_mark_price(persisted, 110.0)
    persisted = await positions.get_by_id(pos.id)
    await manager.update_mark_price(persisted, 95.0)
    await manager.close_position(pos.id, 105.0, ExitReason.TAKE_PROFIT)

    entries = await journals.get_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["duration_seconds"] >= 3590
    assert entry["mfe"] == pytest.approx(20.0)
    assert entry["mae"] == pytest.approx(10.0)
    assert "regime:RISK_OFF" in entry["tags"]

    await journal_service.stop()
    await db.close()
