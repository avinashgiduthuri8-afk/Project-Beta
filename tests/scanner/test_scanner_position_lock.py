"""
tests/test_v2_scanner_position_lock.py
Unit tests verifying B7 Early Single-Coin Position Lock Suppression before C2/AI compute.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest

from core.bus.event_bus import EventBus
from core.config import AppConfig
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.types import BotMode, BotName, Position
from scanner.service import ScannerService

TEST_DB_DIR = os.path.abspath(".test_dbs")
os.makedirs(TEST_DB_DIR, exist_ok=True)


@pytest.mark.anyio
async def test_early_lock_suppresses_c2_and_ai_evaluation():
    """Verify that a coin with an active open position is suppressed BEFORE C2 evaluation & AI compute."""
    db_file = os.path.join(TEST_DB_DIR, f"db_lock_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    sig_repo = SignalRepository(db.connection)
    event_repo = EventLogRepository(db.connection)

    cfg = AppConfig(
        v2_db_path=db_file,
        v2_scanner_strict_confluence_threshold=80,
    )

    scanner = ScannerService(
        bus=bus,
        signal_repo=sig_repo,
        event_log_repo=event_repo,
        position_repo=pos_repo,
        config=cfg,
    )

    # 1. Insert open position for SOL
    pos = Position(
        id="pos-sol-active",
        coin="SOL",
        pair="SOL/INR",
        bot=BotName.STE,
        mode=BotMode.PAPER,
        entry_price=12000.0,
        entry_time=datetime.now(timezone.utc),
        qty=0.05,
        current_price=12100.0,
        unrealised_pnl=5.0,
    )
    await pos_repo.insert(pos)

    # Mock raw candidates returned from market data (SOL, BTC, ETH)
    async def mock_fetch_v1(**kwargs):
        return [
            {
                "coin": "SOL",
                "pair": "SOL/INR",
                "score": 95,
                "price": 12100.0,
                "timeframe": "15m",
                "market_state": "bull_trend",
                "opportunity_type": "momentum_trade",
                "priority": "Elite",
            },
            {
                "coin": "BTC",
                "pair": "BTC/INR",
                "score": 90,
                "price": 8500000.0,
                "timeframe": "15m",
                "market_state": "bull_trend",
                "opportunity_type": "momentum_trade",
                "priority": "Elite",
            },
        ]

    scanner._fetch_v1_signals = mock_fetch_v1
    scanner._generate_native_candidates = mock_fetch_v1

    # Track what C2 receives
    original_eval = scanner._confluence_engine.evaluate_candidates
    evaluated_coins = []

    def mock_eval(raw_candidates, signals, **kwargs):
        for s in signals:
            evaluated_coins.append(s.coin)
        return original_eval(raw_candidates, signals, **kwargs)

    scanner._confluence_engine.evaluate_candidates = mock_eval

    # Run poll
    summary = await scanner.poll()

    # SOL should NOT have been evaluated by C2 at all (early suppressed due to open position)
    assert "SOL" not in evaluated_coins
    assert "BTC" in evaluated_coins

    await db.close()
