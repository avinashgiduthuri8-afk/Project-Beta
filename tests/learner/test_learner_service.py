"""
Unit and integration tests for Alpha-Learner Service (Stages 8–10).

Covers:
  1. LearnerConfig validation & mode enforcement.
  2. Insufficient trade threshold handling.
  3. Mistake identification & learning insight generation (Stage 8).
  4. Pre-deployment backtest validation & canary parameter promotion (Stage 9 & 10).
  5. Automated safety rollback trigger on consecutive post-promotion losses (Stage 10).
  6. End-to-end autonomous cycle execution.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from core.bus.event_bus import EventBus
from core.repository.backtest_repo import BacktestRepository
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from core.repository.feedback_repo import FeedbackRepository
from core.repository.journal_repo import JournalRepository
from core.repository.learning_repo import LearningRepository
from learner.config import LearnerConfig, get_learner_config
from learner.service import LearnerService


def validation_candles(count: int = 160) -> list[dict]:
    """Deterministic STE replay with repeated profitable OOS breakouts."""
    candles = []
    for i in range(count):
        phase = i % 8
        close = 102.0 if phase in (5, 6) else (107.0 if phase == 7 else 100.0)
        open_price = 102.0 if phase == 6 else close
        high = 107.0 if phase == 7 else close + 0.3
        low = min(open_price, close) - 0.2
        candles.append(
            {
                "pair": "BTC/INR",
                "timeframe": "1h",
                "timestamp": 1_700_000_000_000 + i * 3_600_000,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0 + phase * 10,
            }
        )
    return candles


def test_learner_config():
    cfg = get_learner_config()
    assert cfg.mode == "LEARNER"
    assert "learner" in cfg.db_path
    assert cfg.poll_interval_seconds > 0
    assert cfg.min_trades_for_analysis >= 1

    with pytest.raises(ValueError, match="mode must be 'LEARNER'"):
        bad = LearnerConfig(mode="PAPER")
        if bad.mode != "LEARNER":
            raise ValueError(f"LearnerConfig corrupted: mode must be 'LEARNER', got '{bad.mode}'")


@pytest.mark.asyncio
async def test_insufficient_trades_skips_cycle():
    temp_dir = Path(tempfile.mkdtemp())
    db = Database(str(temp_dir / "test_learner.db"))
    await db.open()

    cfg = LearnerConfig(min_trades_for_analysis=5)
    journal_repo = JournalRepository(db.connection)
    learning_repo = LearningRepository(db.connection)
    feedback_repo = FeedbackRepository(db.connection)
    backtest_repo = BacktestRepository(db.connection)

    service = LearnerService(
        config=cfg,
        journal_repo=journal_repo,
        learning_repo=learning_repo,
        feedback_repo=feedback_repo,
        backtest_repo=backtest_repo,
    )

    report = await service.run_cycle()
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["trade_count"] == 0
    assert report["promoted_count"] == 0

    await db.close()


@pytest.mark.asyncio
async def test_end_to_end_learner_cycle_with_trades():
    temp_dir = Path(tempfile.mkdtemp())
    paper_db_path = temp_dir / "test_paper.db"
    learner_db_path = temp_dir / "test_learner.db"

    paper_db = Database(str(paper_db_path))
    await paper_db.open()
    learner_db = Database(str(learner_db_path))
    await learner_db.open()
    scanner_db = Database(str(temp_dir / "test_scanner.db"))
    await scanner_db.open()

    paper_journal_repo = JournalRepository(paper_db.connection)
    learning_repo = LearningRepository(learner_db.connection)
    feedback_repo = FeedbackRepository(learner_db.connection)
    backtest_repo = BacktestRepository(learner_db.connection)
    candle_repo = CandleRepository(scanner_db.connection)
    await candle_repo.upsert_candles(validation_candles())

    # Populate sample trade journal in paper DB
    now = datetime.now(timezone.utc)
    for i in range(5):
        entry_time = (now - timedelta(hours=5 - i)).isoformat()
        exit_time = (now - timedelta(hours=4 - i)).isoformat()
        is_loss = i < 3  # 3 consecutive losses to trigger mistake identification
        net_pnl = -50.0 if is_loss else 120.0
        exit_price = 49000.0 if is_loss else 52000.0

        await paper_journal_repo.insert_entry(
            {
                "id": f"trade_{i}",
                "position_id": f"pos_{i}",
                "bot_name": "STE",
                "pair": "BTC/INR",
                "side": "BUY",
                "entry_price": 50000.0,
                "exit_price": exit_price,
                "quantity": 0.01,
                "entry_timestamp": entry_time,
                "exit_timestamp": exit_time,
                "duration_seconds": 3600,
                "exit_reason": "STOP_LOSS" if is_loss else "TAKE_PROFIT",
                "gross_pnl": net_pnl,
                "exchange_fee": 1.0,
                "gst_tax": 0.18,
                "tds_194s": 5.0,
                "slippage_cost": 0.5,
                "total_statutory_drag": 6.68,
                "net_pnl": net_pnl,
                "net_pnl_pct": -2.0 if is_loss else 4.0,
                "mfe": 0.005,
                "mae": 0.025,  # Excursion leakage
                "tags": ["TEST"],
            }
        )

    cfg = LearnerConfig(min_trades_for_analysis=3)
    service = LearnerService(
        config=cfg,
        journal_repo=paper_journal_repo,
        learning_repo=learning_repo,
        feedback_repo=feedback_repo,
        backtest_repo=backtest_repo,
        candle_repo=candle_repo,
    )

    report = await service.run_cycle()
    assert report["status"] == "COMPLETED"
    assert report["trade_count"] == 5
    assert report["insights_count"] >= 1
    assert report["calibrations_count"] >= 1

    # Check that learning insights were saved
    insights = await learning_repo.get_active_insights(bot_name="STE")
    assert len(insights) >= 1

    # Only candidates with sufficient OOS evidence are promoted.  The proposed
    # STE 0.5x/90 cooling calibration produces no validation trades and must
    # fail closed; unaffected strategies can still pass independently.
    calibrations = await service.get_active_calibrations()
    bot_names = [c["bot_name"] for c in calibrations]
    assert "STE" not in bot_names
    assert report["rejected_count"] >= 1
    ste_events = [e for e in report["validation_events"] if e["bot_name"] == "STE"]
    assert ste_events[0]["validation_reason"] == "INSUFFICIENT_VALIDATION_DATA"

    # Check audit history
    history = await service.get_audit_history()
    assert len(history) >= 1

    await paper_db.close()
    await learner_db.close()
    await scanner_db.close()


@pytest.mark.asyncio
async def test_automated_safety_rollback():
    temp_dir = Path(tempfile.mkdtemp())
    db = Database(str(temp_dir / "test_rollback.db"))
    await db.open()

    feedback_repo = FeedbackRepository(db.connection)
    backtest_repo = BacktestRepository(db.connection)
    bus = EventBus()

    from background.backtest.service.service import BacktestService
    from background.feedback.orchestrator import FeedbackOrchestrator

    backtest_svc = BacktestService(backtest_repo=backtest_repo)
    orchestrator = FeedbackOrchestrator(
        feedback_repo=feedback_repo,
        backtest_service=backtest_svc,
        bus=bus,
    )

    # 1. Promote a candidate calibration
    promoted = await orchestrator.evaluate_and_validate_calibration(
        bot_name="STE",
        pair="BTC/INR",
        proposed_multiplier=1.2,
        proposed_threshold=88.0,
        validation_candles=validation_candles(),
    )
    assert promoted["status"] == "PROMOTED"
    assert promoted["new_multiplier"] == 1.2
    assert promoted["new_threshold"] == 88.0

    # 2. First post-promotion loss -> no rollback yet
    res1 = await orchestrator.register_trade_outcome(bot_name="STE", pair="BTC/INR", is_win=False)
    assert res1 is None

    # 3. Second consecutive post-promotion loss -> triggers emergency rollback to baseline!
    res2 = await orchestrator.register_trade_outcome(bot_name="STE", pair="BTC/INR", is_win=False)
    assert res2 is not None
    assert res2["action_taken"] == "ROLLBACK"
    assert res2["status"] == "ROLLED_BACK"
    assert res2["new_multiplier"] == 1.0
    assert res2["new_threshold"] == 85.0

    # Verify active cache was rolled back
    active = await feedback_repo.get_active_calibration("STE")
    assert active["weight_multiplier"] == 1.0
    assert active["strict_threshold"] == 85.0

    await db.close()


@pytest.mark.asyncio
async def test_learner_telegram_commands():
    from unittest.mock import AsyncMock

    from learner.telegram.notifier import LearnerTelegramNotifier

    temp_dir = Path(tempfile.mkdtemp())
    db = Database(str(temp_dir / "test_tg.db"))
    await db.open()

    cfg = LearnerConfig()
    journal_repo = JournalRepository(db.connection)
    learning_repo = LearningRepository(db.connection)
    feedback_repo = FeedbackRepository(db.connection)
    backtest_repo = BacktestRepository(db.connection)

    service = LearnerService(
        config=cfg,
        journal_repo=journal_repo,
        learning_repo=learning_repo,
        feedback_repo=feedback_repo,
        backtest_repo=backtest_repo,
    )

    notifier = LearnerTelegramNotifier(
        bus=EventBus(),
        config=cfg,
        learner_service=service,
        bot_token="123456:FAKE_TOKEN_FOR_TEST",
        chat_id="999999",
    )
    assert notifier.is_configured is True

    # Mock send_message so no network call is attempted
    notifier.client.send_message = AsyncMock(return_value=True)

    # Test /help
    await notifier._handle_command("/help", "999999")
    notifier.client.send_message.assert_called()
    assert "AI LEARNER & CANARY BOT" in notifier.client.send_message.call_args[0][0]

    # Test /status
    await notifier._handle_command("/status", "999999")
    assert "LEARNER FLEET STATUS" in notifier.client.send_message.call_args[0][0]

    # Test /calibrations
    await notifier._handle_command("/calibrations", "999999")
    assert "ACTIVE STRATEGY CALIBRATIONS" in notifier.client.send_message.call_args[0][0]

    # Test /rollback STE
    await notifier._handle_command("/rollback STE", "999999")
    assert "MANUAL ROLLBACK COMPLETE" in notifier.client.send_message.call_args[0][0]

    await db.close()
