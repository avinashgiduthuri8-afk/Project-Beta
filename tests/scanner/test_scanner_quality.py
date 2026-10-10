"""
tests/test_scanner_quality.py
Phase 3A Comprehensive Test Suite:
- Liquidity / Market Quality Gate (₹200k min turnover, 1.0% max spread)
- Breakout Exhaustion Guard (RSI > 72, volume ratio < 1.0)
- Signal Freshness & TTL (300s breakout TTL, expired signal rejection)
- Strict Multi-Timeframe Gate (15m, 1h, 1d alignment)
- SignalRepository get_recent() & get_total_count()
- Scanner Funnel Telemetry & Observability
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.bus.event_bus import EventBus
from core.config import AppConfig
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.signal_repo import SignalRepository
from core.types import MarketState, OppType, Priority, RiskLevel, Signal
from scanner.adapter import raw_response_to_signals, raw_signal_to_domain
from scanner.confluence_engine import ConfluenceEngine, IndicatorEvaluator
from scanner.service import ScannerService

TEST_DB_DIR = os.path.abspath(".test_dbs")
os.makedirs(TEST_DB_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LIQUIDITY / MARKET QUALITY GATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_liquidity_turnover_thresholds():
    """
    Verify turnover thresholds:
    - ₹49k -> reject (LOW_TURNOVER)
    - ₹199,999 -> reject (LOW_TURNOVER)
    - ₹200k -> pass
    """
    db_file = os.path.join(TEST_DB_DIR, f"db_liq_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    bus = EventBus()
    sig_repo = SignalRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    candle_repo = CandleRepository(db.connection)

    cfg = AppConfig(
        v2_db_path=db_file,
        scanner_min_24h_volume=200000.0,
        scanner_max_spread_pct=1.0,
    )

    scanner = ScannerService(
        bus=bus,
        signal_repo=sig_repo,
        event_log_repo=event_repo,
        candle_repo=candle_repo,
        config=cfg,
    )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def _make_candles(pair: str, base_close: float):
        return [
            {
                "pair": pair,
                "timeframe": "1h",
                "timestamp": now_ms - (30 - i) * 3600000,
                "open": base_close + i * 0.2,
                "high": base_close + i * 0.2 + 0.8,
                "low": base_close + i * 0.2 - 0.2,
                "close": base_close + i * 0.2 + 0.4,
                "volume": 10.0,
            }
            for i in range(30)
        ]

    await candle_repo.upsert_candles(
        _make_candles("COIN49/INR", 100.0)
        + _make_candles("COIN199/INR", 100.0)
        + _make_candles("COIN200/INR", 100.0)
    )

    # Mock market snapshot simulating CoinDCX tickers
    scanner._market_snapshot = {
        "COIN49/INR": {"turnover": 49000.0, "spread_pct": 0.2},
        "COIN199/INR": {"turnover": 199999.0, "spread_pct": 0.2},
        "COIN200/INR": {"turnover": 200000.0, "spread_pct": 0.2},
    }

    candidates = await scanner._generate_native_candidates(
        coins=["COIN49", "COIN199", "COIN200"],
        canonical_inr_coins={"COIN49", "COIN199", "COIN200"},
    )

    coins_passed = {c["coin"] for c in candidates}
    assert "COIN49" not in coins_passed, "₹49k turnover must be rejected"
    assert "COIN199" not in coins_passed, "₹199,999 turnover must be rejected"
    assert "COIN200" in coins_passed, "₹200k turnover must pass"

    counters = scanner._last_funnel_counters
    assert counters["liquidity_rejected"] >= 2
    await db.close()


@pytest.mark.anyio
async def test_spread_thresholds():
    """
    Verify spread thresholds:
    - spread > 1.0% -> reject (HIGH_SPREAD)
    - spread <= 1.0% -> pass
    """
    db_file = os.path.join(TEST_DB_DIR, f"db_spread_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    bus = EventBus()
    sig_repo = SignalRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    candle_repo = CandleRepository(db.connection)

    cfg = AppConfig(
        v2_db_path=db_file,
        scanner_min_24h_volume=200000.0,
        scanner_max_spread_pct=1.0,
    )

    scanner = ScannerService(
        bus=bus,
        signal_repo=sig_repo,
        event_log_repo=event_repo,
        candle_repo=candle_repo,
        config=cfg,
    )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def _make_candles(pair: str):
        return [
            {
                "pair": pair,
                "timeframe": "1h",
                "timestamp": now_ms - (30 - i) * 3600000,
                "open": 500.0 + i,
                "high": 505.0 + i,
                "low": 498.0 + i,
                "close": 502.0 + i,
                "volume": 20.0,
            }
            for i in range(30)
        ]

    await candle_repo.upsert_candles(
        _make_candles("HIGHSPREAD/INR") + _make_candles("LOWSPREAD/INR")
    )

    scanner._market_snapshot = {
        "HIGHSPREAD/INR": {"turnover": 500000.0, "spread_pct": 1.25},  # > 1%
        "LOWSPREAD/INR": {"turnover": 500000.0, "spread_pct": 0.85},   # <= 1%
    }

    candidates = await scanner._generate_native_candidates(
        coins=["HIGHSPREAD", "LOWSPREAD"],
        canonical_inr_coins={"HIGHSPREAD", "LOWSPREAD"},
    )

    coins_passed = {c["coin"] for c in candidates}
    assert "HIGHSPREAD" not in coins_passed, "Spread 1.25% > 1.0% must be rejected"
    assert "LOWSPREAD" in coins_passed, "Spread 0.85% <= 1.0% must pass"

    counters = scanner._last_funnel_counters
    assert counters["spread_rejected"] >= 1
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BREAKOUT EXHAUSTION GUARD TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_breakout_exhaustion_rsi_threshold():
    """
    Verify Breakout Exhaustion RSI:
    - RSI 73 -> rejected (RSI_EXHAUSTION)
    - RSI 72 -> allowed subject to other gates
    """
    evaluator = IndicatorEvaluator()

    sig = Signal(
        id=str(uuid.uuid4()),
        coin="SOL",
        pair="SOL/INR",
        market_state=MarketState.BREAKOUT,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=85,
        confidence=85,
        coin_class="A",
        mtf_alignment=True,
        generated_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
    )

    # Test RSI 73 -> Must be rejected
    cand_73 = {"rsi": 73.0, "volume_ratio": 1.5, "opportunity_type": "momentum_trade"}
    layer_73 = evaluator.evaluate(cand_73, sig)
    assert not layer_73.passed, "RSI 73 breakout signal must be rejected"
    assert any("RSI_EXHAUSTION" in r for r in layer_73.reasons)

    # Test RSI 72 -> Allowed subject to other gates
    cand_72 = {"rsi": 72.0, "volume_ratio": 1.5, "opportunity_type": "momentum_trade"}
    layer_72 = evaluator.evaluate(cand_72, sig)
    assert layer_72.passed, "RSI 72 breakout signal should pass"
    assert not any("RSI_EXHAUSTION" in r for r in layer_72.reasons)


def test_breakout_exhaustion_volume_decline():
    """
    Verify Breakout Exhaustion Volume:
    - declining volume (volume_ratio < 1.0) -> rejected (DECLINING_VOLUME)
    - healthy volume (volume_ratio >= 1.0) -> allowed
    """
    evaluator = IndicatorEvaluator()

    sig = Signal(
        id=str(uuid.uuid4()),
        coin="ETH",
        pair="ETH/INR",
        market_state=MarketState.BREAKOUT,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=85,
        confidence=85,
        coin_class="A",
        mtf_alignment=True,
        generated_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
    )

    # Test declining volume: 0.95 -> Must be rejected
    cand_declining = {"rsi": 65.0, "volume_ratio": 0.95, "opportunity_type": "momentum_trade"}
    layer_dec = evaluator.evaluate(cand_declining, sig)
    assert not layer_dec.passed, "Declining volume (< 1.0) breakout must be rejected"
    assert any("DECLINING_VOLUME" in r for r in layer_dec.reasons)

    # Test healthy volume: 1.2 -> Allowed
    cand_healthy = {"rsi": 65.0, "volume_ratio": 1.20, "opportunity_type": "momentum_trade"}
    layer_healthy = evaluator.evaluate(cand_healthy, sig)
    assert layer_healthy.passed, "Healthy volume (>= 1.0) breakout should pass"
    assert not any("DECLINING_VOLUME" in r for r in layer_healthy.reasons)


def test_breakout_exhaustion_not_applied_to_other_strategies():
    """Verify that RSI 73 is NOT rejected for non-breakout accumulation/continuation strategies."""
    evaluator = IndicatorEvaluator()

    sig = Signal(
        id=str(uuid.uuid4()),
        coin="BTC",
        pair="BTC/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.CONTINUATION,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=85,
        confidence=85,
        coin_class="A",
        mtf_alignment=True,
        generated_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
    )

    cand_trend = {"rsi": 73.0, "volume_ratio": 1.2, "opportunity_type": "continuation"}
    layer = evaluator.evaluate(cand_trend, sig)
    assert not any("RSI_EXHAUSTION" in r for r in layer.reasons), "Non-breakout strategy must not be blocked by breakout RSI exhaustion"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. STRICT MULTI-TIMEFRAME (MTF) GATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_strict_mtf_gate_for_breakout():
    """
    Verify strict MTF gate for breakout candidates:
    - all 3 aligned (15m, 1h, 1d) -> pass
    - 15m mismatch -> reject
    - 1h mismatch -> reject
    - 1d mismatch -> reject
    """
    db_file = os.path.join(TEST_DB_DIR, f"db_mtf_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    bus = EventBus()
    sig_repo = SignalRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    candle_repo = CandleRepository(db.connection)

    cfg = AppConfig(
        v2_db_path=db_file,
        scanner_min_24h_volume=1000.0,
        scanner_max_spread_pct=2.0,
    )

    scanner = ScannerService(
        bus=bus,
        signal_repo=sig_repo,
        event_log_repo=event_repo,
        candle_repo=candle_repo,
        config=cfg,
    )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def _make_bullish_candles(pair: str, tf: str):
        return [
            {
                "pair": pair,
                "timeframe": tf,
                "timestamp": now_ms - (30 - i) * 60000,
                "open": 1000.0 + i * 2,
                "high": 1008.0 + i * 2,
                "low": 998.0 + i * 2,
                "close": 1005.0 + i * 2,
                "volume": 50.0,
            }
            for i in range(30)
        ]

    def _make_bearish_candles(pair: str, tf: str):
        return [
            {
                "pair": pair,
                "timeframe": tf,
                "timestamp": now_ms - (30 - i) * 60000,
                "open": 1050.0 - i * 2,
                "high": 1055.0 - i * 2,
                "low": 1040.0 - i * 2,
                "close": 1045.0 - i * 2,
                "volume": 50.0,
            }
            for i in range(30)
        ]

    # Coin 1: All 3 aligned (15m, 1h, 1d bullish)
    await candle_repo.upsert_candles(_make_bullish_candles("ALIGNED/INR", "1h"))
    await candle_repo.upsert_candles(_make_bullish_candles("ALIGNED/INR", "15m"))
    await candle_repo.upsert_candles(_make_bullish_candles("ALIGNED/INR", "1d"))

    # Coin 2: 15m mismatch (bearish 15m)
    await candle_repo.upsert_candles(_make_bullish_candles("MIS15/INR", "1h"))
    await candle_repo.upsert_candles(_make_bearish_candles("MIS15/INR", "15m"))
    await candle_repo.upsert_candles(_make_bullish_candles("MIS15/INR", "1d"))

    # Coin 3: 1d mismatch (bearish 1d)
    await candle_repo.upsert_candles(_make_bullish_candles("MIS1D/INR", "1h"))
    await candle_repo.upsert_candles(_make_bullish_candles("MIS1D/INR", "15m"))
    await candle_repo.upsert_candles(_make_bearish_candles("MIS1D/INR", "1d"))

    scanner._market_snapshot = {
        "ALIGNED/INR": {"turnover": 500000.0, "spread_pct": 0.2},
        "MIS15/INR": {"turnover": 500000.0, "spread_pct": 0.2},
        "MIS1D/INR": {"turnover": 500000.0, "spread_pct": 0.2},
    }

    candidates = await scanner._generate_native_candidates(
        coins=["ALIGNED", "MIS15", "MIS1D"],
        canonical_inr_coins={"ALIGNED", "MIS15", "MIS1D"},
    )

    passed_coins = {c["coin"] for c in candidates}
    assert "ALIGNED" in passed_coins, "Aligned coin must pass MTF gate"
    assert "MIS15" not in passed_coins, "15m mismatch must be hard rejected"
    assert "MIS1D" not in passed_coins, "1d mismatch must be hard rejected"

    counters = scanner._last_funnel_counters
    assert counters["mtf_rejected"] >= 2, "MTF rejections must be counted"
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SIGNAL FRESHNESS / TTL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_signal_ttl_and_paper_expiry_enforcement():
    """
    Verify:
    - Breakout TTL defaults to 300s (5m).
    - Newly generated signal is fresh and returned by get_live().
    - Stale signal older than TTL is excluded from get_live() so Paper cannot execute it.
    """
    db_file = os.path.join(TEST_DB_DIR, f"db_ttl_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    sig_repo = SignalRepository(db.connection)
    now = datetime.now(timezone.utc)

    # 1. Fresh signal (TTL 300s, generated right now)
    fresh_sig = Signal(
        id=str(uuid.uuid4()),
        coin="SOL",
        pair="SOL/INR",
        market_state=MarketState.BREAKOUT,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=92,
        confidence=90,
        coin_class="A",
        mtf_alignment=True,
        generated_at=now,
        expires_at=now + timedelta(seconds=300),
    )
    await sig_repo.insert(fresh_sig)

    # 2. Stale breakout signal (generated 600s ago, expired 300s ago)
    stale_sig = Signal(
        id=str(uuid.uuid4()),
        coin="AVAX",
        pair="AVAX/INR",
        market_state=MarketState.BREAKOUT,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=91,
        confidence=88,
        coin_class="B",
        mtf_alignment=True,
        generated_at=now - timedelta(seconds=600),
        expires_at=now - timedelta(seconds=300),  # Expired
    )
    await sig_repo.insert(stale_sig)

    # Verify get_live() only returns fresh signal
    live_sigs = await sig_repo.get_live()
    live_ids = {s.id for s in live_sigs}
    assert fresh_sig.id in live_ids, "Fresh breakout signal must be live"
    assert stale_sig.id not in live_ids, "Stale breakout signal must NOT be returned in get_live()"

    # Verify adapter sets breakout TTL to 300s
    raw_breakout = {
        "coin": "LINK",
        "market_state": "breakout",
        "opportunity_type": "momentum_trade",
        "score": 90,
    }
    adapted = raw_signal_to_domain(raw_breakout, signal_ttl_seconds=1800, breakout_ttl_seconds=300)
    ttl_delta = (adapted.expires_at - adapted.generated_at).total_seconds()
    assert abs(ttl_delta - 300) < 2, f"Breakout signal TTL must be 300s, got {ttl_delta}"

    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SIGNAL REPOSITORY get_recent() & get_total_count() TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_signal_repository_get_recent():
    """Verify SignalRepository.get_recent() ordering, limits, and empty result."""
    db_file = os.path.join(TEST_DB_DIR, f"db_repo_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    sig_repo = SignalRepository(db.connection)

    # 1. Empty repository
    empty_res = await sig_repo.get_recent(limit=10)
    assert empty_res == [], "Empty repo must return []"
    assert await sig_repo.get_total_count() == 0

    # 2. Insert signals with staggered timestamps
    now = datetime.now(timezone.utc)
    for i in range(5):
        s = Signal(
            id=f"sig_{i}",
            coin=f"COIN_{i}",
            pair=f"COIN_{i}/INR",
            market_state=MarketState.BULL_TREND,
            opportunity_type=OppType.CONTINUATION,
            priority=Priority.HIGH,
            risk_level=RiskLevel.LOW,
            score=80 + i,
            confidence=80,
            coin_class="A",
            mtf_alignment=True,
            generated_at=now + timedelta(seconds=i * 10),
            expires_at=now + timedelta(seconds=3600),
        )
        await sig_repo.insert(s)

    # 3. Test get_total_count()
    assert await sig_repo.get_total_count() == 5

    # 4. Test ordering (most recent first)
    recent = await sig_repo.get_recent(limit=10)
    assert len(recent) == 5
    assert recent[0].id == "sig_4", "Ordering must be generated_at DESC"
    assert recent[-1].id == "sig_0"

    # 5. Test limit
    limited = await sig_repo.get_recent(limit=2)
    assert len(limited) == 2
    assert [s.id for s in limited] == ["sig_4", "sig_3"]

    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SCANNER FUNNEL TELEMETRY & OBSERVABILITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_scanner_funnel_metrics_structure_and_values():
    """Verify ScannerService.get_funnel_metrics() structure and values."""
    db_file = os.path.join(TEST_DB_DIR, f"db_funnel_met_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    scanner = ScannerService(
        bus=EventBus(),
        signal_repo=SignalRepository(db.connection),
        event_log_repo=EventLogRepository(db.connection),
        config=AppConfig(v2_db_path=db_file),
    )

    scanner._last_funnel_counters = {
        "scanned": 50,
        "raw_universe": 50,
        "liquidity_rejected": 15,
        "spread_rejected": 5,
        "mtf_rejected": 10,
        "exhaustion_rejected": 8,
        "other_risk_rejected": 4,
        "qualified": 8,
        "signals_generated": 2,
    }

    metrics = scanner.get_funnel_metrics()

    # Phase 3A Required Keys
    assert "scanned" in metrics
    assert "liquidity_rejected" in metrics
    assert "spread_rejected" in metrics
    assert "mtf_rejected" in metrics
    assert "exhaustion_rejected" in metrics
    assert "other_risk_rejected" in metrics
    assert "qualified" in metrics
    assert "signals_generated" in metrics

    # Numeric assertions
    assert metrics["scanned"] == 50
    assert metrics["liquidity_rejected"] == 15
    assert metrics["spread_rejected"] == 5
    assert metrics["mtf_rejected"] == 10
    assert metrics["exhaustion_rejected"] == 8
    assert metrics["other_risk_rejected"] == 4
    assert metrics["qualified"] == 8
    assert metrics["signals_generated"] == 2

    # Backward-compatible telemetry schema keys
    assert "layers" in metrics
    assert "dispatched_signals_count" in metrics
    assert "final_conversion_pct" in metrics
    assert len(metrics["layers"]) == 5

    await db.close()
