"""
Regression test for PROJECT-ALPHA:
Verifies the complete pipeline:
  Candles -> Native Candidate Generation -> Confluence Engine -> Signal
  -> AI Intelligence -> Risk Engine -> Trading Service -> Paper Position.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from background.ai.service import AIIntelligenceService
from core.bus.event_bus import EventBus
from core.bus.subscribers import register_all
from core.config import AppConfig
from core.repository.ai_repo import AIAnalysisRepository
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    MarketState,
)
from execution.risk.service import RiskService
from execution.service import TradingService
from scanner.service import ScannerService


@pytest.mark.anyio
async def test_complete_signal_to_paper_trade_pipeline():
    test_db = Path(tempfile.gettempdir()) / f"test_pipe_{uuid.uuid4().hex}.db"
    db = Database(str(test_db))
    await db.open()
    conn = db.connection

    # Repositories
    candle_repo = CandleRepository(conn)
    signal_repo = SignalRepository(conn)
    position_repo = PositionRepository(conn)
    trade_repo = TradeRepository(conn)
    event_log_repo = EventLogRepository(conn)
    ai_repo = AIAnalysisRepository(conn)

    # Seed 1h, 4h, and 1d bullish breakout candles for BTC/INR
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    base_price = 7800000.0

    step_15m = 60 * 60 * 1000
    candles_15m = []
    price = base_price
    for i in range(30):
        ts = now_ms - (30 - i) * step_15m
        if i % 2 == 0 and i > 0:
            price -= 15000.0  # healthy pullback
        else:
            price += 25000.0  # upward continuation
        vol = 25.0 if i >= 27 else 10.0
        candles_15m.append(
            {
                "pair": "BTC/INR",
                "timeframe": "15m",
                "timestamp": ts,
                "open": price - 20000.0,
                "high": price + 50000.0,
                "low": price - 50000.0,
                "close": price,
                "volume": vol,
            }
        )
    await candle_repo.upsert_candles(candles_15m)

    step_1h = 4 * 60 * 60 * 1000
    candles_1h = []
    price_1h = base_price
    for i in range(30):
        ts = now_ms - (30 - i) * step_1h
        if i % 2 == 0 and i > 0:
            price_1h -= 15000.0
        else:
            price_1h += 25000.0
        vol = 50.0 if i >= 27 else 20.0
        candles_1h.append(
            {
                "pair": "BTC/INR",
                "timeframe": "1h",
                "timestamp": ts,
                "open": price_1h - 20000.0,
                "high": price_1h + 50000.0,
                "low": price_1h - 50000.0,
                "close": price_1h,
                "volume": vol,
            }
        )
    await candle_repo.upsert_candles(candles_1h)

    step_1d = 24 * 60 * 60 * 1000
    candles_1d = []
    p_1d = base_price
    for i in range(10):
        ts = now_ms - (10 - i) * step_1d
        if i % 3 == 0 and i > 0:
            p_1d -= 5000.0
        else:
            p_1d += 12000.0
        candles_1d.append(
            {
                "pair": "BTC/INR",
                "timeframe": "1d",
                "timestamp": ts,
                "open": p_1d - 2000.0,
                "high": p_1d + 5000.0,
                "low": p_1d - 3000.0,
                "close": p_1d,
                "volume": 100.0,
            }
        )
    await candle_repo.upsert_candles(candles_1d)

    # EventBus and Services
    bus = EventBus()
    config = AppConfig(
        v2_deployment_mode="PAPER",
        v2_ai_enabled=True,
        v2_ai_confidence_threshold=60,
        order_size_inr=200.0,
        enforce_single_coin_lock=True,
    )

    scanner_service = ScannerService(
        config=config,
        bus=bus,
        signal_repo=signal_repo,
        event_log_repo=event_log_repo,
        candle_repo=candle_repo,
    )

    ai_service = AIIntelligenceService(
        config=config,
        bus=bus,
        ai_repo=ai_repo,
        signal_repo=signal_repo,
        event_log_repo=event_log_repo,
    )

    risk_service = RiskService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=config,
    )

    trading_service = TradingService(
        bus=bus,
        position_repo=position_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log_repo,
        config=config,
    )

    # Wire bus
    register_all(
        bus=bus,
        ai_service=ai_service,
        risk_service=risk_service,
        trading_service=trading_service,
    )

    # Override external network calls for deterministic local testing
    scanner_service._fetch_watchlist_coins = AsyncMock(return_value=["BTC"])
    scanner_service._fetch_coindcx_candles = AsyncMock(return_value=[])
    scanner_service._news_risk_service.fetch_latest_news = AsyncMock()
    scanner_service.bootstrap_candles = AsyncMock()
    scanner_service._candle_flusher_loop = AsyncMock()
    scanner_service._calibration_worker.start = AsyncMock()
    scanner_service._calibration_worker.stop = AsyncMock()
    scanner_service._market_context_service.refresh_market_context = AsyncMock(
        return_value={
            "btc_trend": "BULLISH",
            "eth_trend": "BULLISH",
            "market_regime": "RISK_ON",
            "fear_and_greed": 65,
        }
    )

    await scanner_service.start()
    await ai_service.start()
    await trading_service.start()

    # Trigger poll
    summary = await scanner_service.poll()

    assert summary["fetched"] == 1
    assert summary["new_signals"] == 1, (
        f"Expected 1 high-conviction signal, got {summary}"
    )

    # Verify signal persisted in repository
    all_signals = await signal_repo.get_by_coin("BTC")
    assert len(all_signals) == 1
    btc_sig = all_signals[0]
    assert btc_sig.mtf_alignment is True
    assert btc_sig.market_state in (MarketState.BREAKOUT, MarketState.BULL_TREND)

    # Let event loop process downstream events
    await asyncio.sleep(0.5)

    # Verify AI evaluation persisted
    ai_analysis = await ai_repo.get_by_signal_id(btc_sig.id)
    assert ai_analysis is not None
    assert ai_analysis.recommendation.value in ("APPROVE", "SCALE_DOWN")

    # Verify Paper Position opened in PositionRepository
    open_positions = await position_repo.get_open()
    assert len(open_positions) == 1, f"Expected 1 open position, got {open_positions}"
    pos = open_positions[0]
    assert pos.coin == "BTC"
    assert pos.mode == BotMode.PAPER
    assert pos.entry_price > 7800000.0  # Real price, not 100.0
    assert pos.qty > 0.0

    # Cleanup
    await scanner_service.stop()
    await ai_service.stop()
    await trading_service.stop()
    await db.close()
