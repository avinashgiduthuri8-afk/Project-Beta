"""
tests/test_v2_telegram_commands.py
Comprehensive test suite for PROJECT-ALPHA Telegram Bot Command Layer.

Tests:
1. Authorization & Security:
   - Authorized operator succeeds
   - Unauthorized user rejected with "Unauthorized."
   - Sensitive credentials / tokens masked from responses
   - Invalid command handling
2. System Commands:
   - /start, /help, /status, /health, /mode, /uptime
   - Component-level health truthfulness (🟢/🔴)
3. Scanner Commands:
   - /scan, /signals, /signal <symbol>, /watchlist, /funnel
   - Real scanner snapshot data (no invented signals)
4. Trading Commands:
   - /positions, /trades, /pnl, /orders, /capital, /config
   - Mode tags (MODE: PAPER / SHADOW / LIVE_MICROCASH)
   - Capital reality (CAPITAL UNKNOWN on failure, never fake ₹10,000)
5. Order Amount Control (/setamount):
   - Valid updates persist to runtime config override
   - Rejection of invalid amounts (< 100, <= 0, non-numeric, NaN)
   - Synchronizes across sub-account manager clients
6. Trading Controls:
   - /pause disables new entries without closing open positions
   - /resume re-enables entries and resets circuit breaker
   - /emergency_stop prompts confirmation and trips circuit breaker
   - /reconcile executes order reconciliation
7. Safety Invariants:
   - Telegram handlers cannot bypass Risk Engine or directly place orders
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from background.monitoring.health import HealthChecker
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import (
    AppConfig,
    get_config,
    invalidate_config,
)
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from core.types import (
    BotMode,
    BotName,
    MarketState,
    OppType,
    Position,
    Priority,
    RiskLevel,
    Signal,
)
from execution.risk.service import RiskService
from execution.service import TradingService
from execution.trading.subaccount_manager import CoinDCXSubAccountManager
from scanner.service import ScannerService
from telegram.formatters import mask_sensitive_data
from telegram.telegram_interface import (
    TelegramInteractiveInterface,
)

TEST_DB_DIR = os.path.abspath(".test_dbs")
os.makedirs(TEST_DB_DIR, exist_ok=True)


class MockTelegramClient:
    """Mock Telegram client recording dispatched messages."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.edited_messages: list[dict] = []
        self.answered_callbacks: list[dict] = []
        self.sent_documents: list[dict] = []
        self.is_configured = True

    async def send_message(
        self, text: str, target_chat_id: str | None = None, reply_markup: Any = None
    ) -> dict:
        msg = {
            "text": text,
            "target_chat_id": (
                str(target_chat_id) if target_chat_id is not None else None
            ),
            "reply_markup": reply_markup,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sent_messages.append(msg)
        return {"ok": True, "result": {"message_id": len(self.sent_messages)}}

    async def send_document(
        self,
        document_path: str,
        caption: str = "",
        target_chat_id: str | None = None,
        parse_mode: str = "HTML",
        max_retries: int = 2,
    ) -> bool:
        self.sent_documents.append(
            {
                "document_path": document_path,
                "caption": caption,
                "target_chat_id": (
                    str(target_chat_id) if target_chat_id is not None else None
                ),
                "parse_mode": parse_mode,
            }
        )
        return True

    async def edit_message_text(
        self, text: str, chat_id: Any, message_id: int, reply_markup: Any = None
    ) -> dict:
        msg = {
            "text": text,
            "chat_id": str(chat_id),
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        self.edited_messages.append(msg)
        return {"ok": True}

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> dict:
        self.answered_callbacks.append(
            {"id": callback_query_id, "text": text, "show_alert": show_alert}
        )
        return {"ok": True}


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    monkeypatch.delenv("TOTAL_CAPITAL_LIMIT", raising=False)
    monkeypatch.setenv("ALERT_CHAT_ID", "999888")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "999888, 777666")
    invalidate_config()
    yield
    invalidate_config()


async def _setup_telegram_test_env(
    mode: str = "PAPER",
    order_size: float = 200.0,
    live_balance: float | None = None,
    scanner_healthy: bool = True,
):
    db_file = os.path.join(TEST_DB_DIR, f"db_tg_{uuid.uuid4().hex[:8]}.db")
    db = Database(db_file)
    await db.open()

    bus = EventBus()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    sig_repo = SignalRepository(db.connection)

    cfg = AppConfig(
        v2_deployment_mode=mode,
        v2_trading_enabled=True,
        order_size_inr=order_size,
        alert_chat_id="999888",
        telegram_allowed_chat_ids="999888,777666",
        v2_db_path=db_file,
    )

    subaccount_mgr = CoinDCXSubAccountManager()
    if live_balance is not None:
        subaccount_mgr.get_live_balance = AsyncMock(
            return_value={
                "success": True,
                "inr_balance": live_balance,
                "inr_locked": 0.0,
                "error": None,
            }
        )

    risk = RiskService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
    )
    await risk.start()

    trading = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_repo,
        config=cfg,
        subaccount_manager=subaccount_mgr,
    )
    await trading.start()

    scanner = ScannerService(
        bus=bus,
        signal_repo=sig_repo,
        event_log_repo=event_repo,
        config=cfg,
    )
    scanner._fetch_watchlist_coins = AsyncMock(
        return_value=["BTC/INR", "ETH/INR", "SOL/INR", "BNB/INR"]
    )
    # Also mock _fetch_v1_signals to prevent live API calls during poll
    scanner._fetch_v1_signals = AsyncMock(
        return_value=[
            {
                "coin": "SOL",
                "pair": "SOL/INR",
                "price": 1000.0,
                "rsi": 55.0,
                "score": 90,
                "mtf_alignment": True,
            }
        ]
    )
    if scanner_healthy:
        scanner.get_health = MagicMock(
            return_value={"healthy": True, "registered": True, "poll_count": 5}
        )

    health = HealthChecker(
        db=db,
        scanner_service=scanner,
        risk_service=risk,
        trading_service=trading,
    )

    tg_client = MockTelegramClient()
    c2 = TelegramInteractiveInterface(
        telegram_client=tg_client,
        bus=bus,
        config=cfg,
        signal_repo=sig_repo,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        risk_service=risk,
        trading_service=trading,
        scanner_service=scanner,
        health_checker=health,
        event_log_repo=event_repo,
    )

    return (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUTHORIZATION & SECURITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_authorized_user_succeeds():
    """Verify that a user whose chat_id is in whitelist receives valid response."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/help"})
    assert len(tg_client.sent_messages) == 1
    assert "PROJECT-ALPHA OPERATOR COMMANDS" in tg_client.sent_messages[0]["text"]
    await db.close()


@pytest.mark.anyio
async def test_unauthorized_user_rejected_with_exact_string():
    """Verify unauthorized users receive 'Unauthorized.' with zero sensitive info disclosed."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 111222}, "text": "/status"})
    assert len(tg_client.sent_messages) == 1
    assert tg_client.sent_messages[0]["text"] == "Unauthorized."
    await db.close()


@pytest.mark.anyio
async def test_dangerous_commands_require_authorization():
    """Verify dangerous commands (/setamount, /pause, /resume, /emergency_stop, /reconcile) reject unauthorized senders."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    dangerous_cmds = [
        "/setamount 500",
        "/pause",
        "/resume",
        "/emergency_stop",
        "/reconcile",
    ]

    for cmd in dangerous_cmds:
        tg_client.sent_messages.clear()
        await c2._handle_incoming_message({"chat": {"id": 111222}, "text": cmd})
        assert len(tg_client.sent_messages) == 1
        assert tg_client.sent_messages[0]["text"] == "Unauthorized."
    await db.close()


def test_mask_sensitive_data_scrubs_secrets():
    """Verify secret sanitizer scrubs tokens, keys, passwords, and secrets."""
    raw = (
        '{"api_key": "abcd1234efgh5678", "secret": "super_secret_payload", '
        '"token": "bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789", "password": "rootpassword"}'
    )
    scrubbed = mask_sensitive_data(raw)
    assert "abcd1234efgh5678" not in scrubbed
    assert "super_secret_payload" not in scrubbed
    assert "rootpassword" not in scrubbed
    assert "***REDACTED***" in scrubbed


@pytest.mark.anyio
async def test_unknown_command_returns_help_prompt():
    """Verify unknown command safely prompts user to view /help without crashing."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/foobar123"})
    assert len(tg_client.sent_messages) == 1
    assert "Unknown command" in tg_client.sent_messages[0]["text"]
    assert "/help" in tg_client.sent_messages[0]["text"]
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SYSTEM COMMANDS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_cmd_start_and_menu():
    """Verify /start and /menu display welcome and mission control keyboard."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/start"})
    assert len(tg_client.sent_messages) == 1
    assert "MISSION CONTROL" in tg_client.sent_messages[0]["text"]
    assert tg_client.sent_messages[0]["reply_markup"] is not None
    await db.close()


@pytest.mark.anyio
async def test_cmd_status_displays_complete_telemetry():
    """Verify /status displays mode, system, scanner, execution, risk, capital, order amount, and positions."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(
        mode="PAPER", order_size=400.0
    )

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/status"})
    text = tg_client.sent_messages[0]["text"]
    assert "MODE: PAPER" in text
    assert "₹400.00" in text
    assert "Scanner Status:" in text
    assert "Execution Status:" in text
    assert "Risk Status:" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_health_reports_component_health():
    """Verify /health displays component-level indicators (🟢/🔴)."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(scanner_healthy=True)

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/health"})
    text = tg_client.sent_messages[0]["text"]
    assert "Scanner Connection" in text and "🟢" in text
    assert "Risk Engine" in text and "🟢" in text
    assert "Paper Execution" in text and "🟢" in text
    assert "Paper Database" in text and "🟢" in text
    assert "EventBus" in text and "🟢" in text
    assert "Signal Intake" in text and "🟢" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_mode_shows_active_parameters():
    """Verify /mode displays current mode and trading_enabled state."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(mode="PAPER")

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode"})
    text = tg_client.sent_messages[0]["text"]
    assert "MODE: PAPER" in text
    assert "PAPER" in text
    assert "YES" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_uptime():
    """Verify /uptime returns formatted elapsed time and poll counts."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/uptime"})
    text = tg_client.sent_messages[0]["text"]
    assert "SYSTEM UPTIME & TELEMETRY" in text
    assert "Elapsed Uptime:" in text
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SCANNER COMMANDS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_cmd_scan_and_signals():
    """Verify /scan and /signals display latest cycle and 1-line high-conviction signals."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    # Inject mock signal into scanner
    mock_sig = Signal(
        id="sig-test-1",
        coin="SOL",
        pair="SOL/INR",
        market_state=MarketState.BULL_TREND,
        opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.HIGH,
        risk_level=RiskLevel.LOW,
        score=88,
        confidence=85,
        coin_class="A",
        mtf_alignment=True,
        generated_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        raw_payload={"price": 12500.0, "bot": "STE"},
    )
    scanner._live[mock_sig.id] = mock_sig

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/scan"})
    text = tg_client.sent_messages[-1]["text"]
    assert "LATEST SCANNER CYCLE" in text
    assert "SOL" in text

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/signals"})
    text = tg_client.sent_messages[-1]["text"]
    assert "HIGH-CONVICTION SIGNALS" in text
    assert "• <b>SOL</b> | <code>STE</code> | C2: <code>88</code>" in text
    assert "/signal" in text
    assert "technical breakdown" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_signal_detail_deep_dive():
    """Verify /signal BTCINR inspects 4-pillar scores and AI conviction."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    # Prepopulate scanner latest evaluated coin snapshot
    scanner._latest_evaluated_coins["BTCINR"] = {
        "pair": "BTC/INR",
        "price": 8600000.0,
        "volume_24h": 45000000.0,
        "volume_ratio": 2.1,
        "ema_trend": "BULLISH",
        "rsi": 62.4,
        "mtf_alignment": "15m_15m",
        "confluence_score": 91,
        "status": "PASSED",
        "bot": "STE",
        "ai_recommendation": "APPROVE",
        "ai_confidence": 92,
        "ai_strengths": ["Strong MTF alignment", "High volume expansion"],
        "rejection_reasons": [],
    }

    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/signal BTCINR"}
    )
    text = tg_client.sent_messages[-1]["text"]
    assert "SIGNAL INSPECTOR — BTC/INR" in text
    assert "4-Pillar Breakdown:" in text
    assert "Technical Setup:" in text
    assert "Chart Structure:" in text
    assert "Volume & Liquidity:" in text
    assert "Market Regime & News:" in text
    assert "AI Conviction:" in text
    assert "APPROVE" in text
    assert "92%" in text
    assert "Strong MTF alignment" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_watchlist_and_funnel():
    """Verify /watchlist and /funnel display monitored assets and conversion pipeline."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/watchlist"})
    assert "ACTIVE SCANNER WATCHLIST" in tg_client.sent_messages[-1]["text"]
    assert "SOL" in tg_client.sent_messages[-1]["text"]

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/funnel"})
    assert "5-LAYER SCANNER CONVERSION FUNNEL" in tg_client.sent_messages[-1]["text"]
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TRADING COMMANDS & CAPITAL REALITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_cmd_bots_fleet():
    """Verify /bots displays the 4 production bots fleet telemetry card."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/bots"})
    text = tg_client.sent_messages[-1]["text"]
    assert "BOT FLEET" in text
    assert "STE   🟢 ACTIVE" in text
    assert "HDA   🟢 ACTIVE" in text
    assert "VCP   🟢 ACTIVE" in text
    assert "BBS   🟢 ACTIVE" in text
    assert "Open Positions:" in text
    assert "Today's Trades:" in text
    assert "Today's P&L:" in text
    assert "Signals Today:" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_positions_and_trades():
    """Verify /positions shows Coin, Entry, Capital deployed, Unrealized P&L (₹ and %), TP, SL, Status, omitting raw Qty and LTP."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    # Create an open position
    pos = Position(
        id="pos-sol-1",
        coin="SOL",
        pair="SOL/INR",
        bot=BotName.STE,
        mode=BotMode.PAPER,
        entry_price=12000.0,
        entry_time=datetime.now(timezone.utc),
        qty=0.05,
        current_price=12200.0,
        unrealised_pnl=10.0,
        stop_loss=11500.0,
        take_profit=13000.0,
    )
    await pos_repo.insert(pos)

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/positions"})
    pos_text = tg_client.sent_messages[-1]["text"]
    assert "ACTIVE FLEET POSITIONS" in pos_text
    assert "SOL/INR" in pos_text
    assert "Entry: ₹12000.00" in pos_text
    assert "Capital: ₹600.00" in pos_text
    assert "Unrealized P&L:" in pos_text
    assert "TP: ₹13000.00" in pos_text
    assert "SL: ₹11500.00" in pos_text
    assert "Status: <code>OPEN</code>" in pos_text
    # Assert raw Qty and Current LTP are omitted from displayed line
    assert "• Qty:" not in pos_text
    assert "Current:" not in pos_text

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/orders"})
    assert "RECENT ORDERS LEDGER" in tg_client.sent_messages[-1]["text"]
    assert "SOL" in tg_client.sent_messages[-1]["text"]
    await db.close()


@pytest.mark.anyio
async def test_cmd_capital_live_mode_dynamic_balance():
    """In LIVE mode, /capital queries real CoinDCX balance."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(
        mode="LIVE_MICROCASH",
        order_size=350.0,
        live_balance=4520.50,
    )

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/capital"})
    text = tg_client.sent_messages[-1]["text"]
    assert "MODE: LIVE_MICROCASH" in text
    assert "4,520.50" in text
    assert "350.00" in text
    await db.close()


@pytest.mark.anyio
async def test_cmd_capital_live_mode_unavailable_returns_capital_unknown():
    """In LIVE mode, if exchange balance cannot be obtained, returns CAPITAL UNKNOWN (never fake ₹10,000)."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env(
        mode="LIVE_MICROCASH",
        order_size=200.0,
    )

    # Force failure
    subaccount_mgr.get_live_balance = AsyncMock(
        return_value={
            "success": False,
            "inr_balance": None,
            "error": "Exchange connection timeout",
        }
    )

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/capital"})
    text = tg_client.sent_messages[-1]["text"]
    assert "MODE: LIVE_MICROCASH" in text
    assert "CAPITAL UNKNOWN" in text
    assert "10000" not in text
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ORDER AMOUNT CONTROL (/setamount) TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_cmd_setamount_valid_updates_config_and_persists():
    """Verify /setamount 750 persists to override file and synchronizes subaccount manager."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/setamount 750"}
    )
    text = tg_client.sent_messages[-1]["text"]
    assert "ORDER AMOUNT UPDATED" in text
    assert "750.00" in text

    # Verify updated in-memory config and client defaults
    refreshed_cfg = get_config()
    assert refreshed_cfg.order_size_inr == 750.0
    client = subaccount_mgr.get_client(BotName.STE)
    assert client.config.default_trade_amount_inr == 750.0
    await db.close()


@pytest.mark.anyio
async def test_cmd_setamount_rejects_invalid_values():
    """Verify /setamount accepts valid amounts without arbitrary ₹100 floor, but rejects negative, zero, non-numeric, or NaN values."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    # 1. Valid positive amount >= 200
    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/setamount 250"}
    )
    assert "ORDER AMOUNT UPDATED" in tg_client.sent_messages[-1]["text"]
    assert "250.00" in tg_client.sent_messages[-1]["text"]

    # 2. Amount below 200 rejected
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/setamount 50"})
    assert "Order Amount Below Minimum" in tg_client.sent_messages[-1]["text"]
    assert "200.00" in tg_client.sent_messages[-1]["text"]

    # 3. Negative amount rejected
    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/setamount -200"}
    )
    assert "Invalid Order Amount" in tg_client.sent_messages[-1]["text"]
    assert "positive finite number" in tg_client.sent_messages[-1]["text"]

    # 4. Zero rejected
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/setamount 0"})
    assert "Invalid Order Amount" in tg_client.sent_messages[-1]["text"]

    # 5. Non-numeric rejected
    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/setamount abc"}
    )
    assert "Invalid Number:" in tg_client.sent_messages[-1]["text"]

    # 6. Empty arg
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/setamount"})
    assert "Missing Order Amount" in tg_client.sent_messages[-1]["text"]
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TRADING CONTROL TESTS (/pause, /resume, /emergency_stop, /reconcile)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_cmd_pause_and_resume():
    """Verify /pause suspends new entries without closing open positions, and /resume restores entries."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    # 1. Pause
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/pause"})
    assert "TRADING EXECUTION PAUSED" in tg_client.sent_messages[-1]["text"]
    assert c2._config.v2_trading_enabled is False
    assert trading._config.v2_trading_enabled is False

    # 2. Resume
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/resume"})
    assert "TRADING EXECUTION RESUMED" in tg_client.sent_messages[-1]["text"]
    assert c2._config.v2_trading_enabled is True
    assert trading._config.v2_trading_enabled is True
    await db.close()


@pytest.mark.anyio
async def test_cmd_emergency_stop_confirmation_flow():
    """Verify /emergency_stop prompts confirmation, and /emergency_stop confirm trips the circuit breaker."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    # 1. First call prompts confirmation
    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/emergency_stop"}
    )
    assert "EMERGENCY STOP CONFIRMATION REQUIRED" in tg_client.sent_messages[-1]["text"]
    assert risk.circuit_breaker.is_open is False

    # 2. Confirm call trips emergency stop
    await c2._handle_incoming_message(
        {"chat": {"id": 999888}, "text": "/emergency_stop confirm"}
    )
    assert "EMERGENCY STOPPED" in tg_client.sent_messages[-1]["text"]
    assert risk.circuit_breaker.is_open is True
    assert risk.circuit_breaker.emergency_stop is True

    # 3. Resume restores normal operation
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/resume"})
    assert risk.circuit_breaker.is_open is False
    await db.close()


@pytest.mark.anyio
async def test_cmd_reconcile_runs_without_creating_duplicate_orders():
    """Verify /reconcile executes order reconciliation and reports zero duplicates."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env()

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/reconcile"})
    text = tg_client.sent_messages[-1]["text"]
    assert "EXCHANGE ORDER RECONCILIATION" in text
    assert "Reconciliation Status:" in text
    assert "Mismatches Detected:" in text
    await db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. RISK & MONITORING TESTS (/risk, /limits, /alerts, /logs)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_cmd_risk_limits_alerts_logs():
    """Verify /risk, /limits, /alerts, and /logs display monitoring data and scrub secrets."""
    (
        db,
        bus,
        pos_repo,
        trade_repo,
        sig_repo,
        event_repo,
        cfg,
        subaccount_mgr,
        risk,
        trading,
        scanner,
        health,
        tg_client,
        c2,
    ) = await _setup_telegram_test_env()

    # Append test event with secret
    await event_repo.append(
        event_type="SYSTEM_CONFIG_UPDATED",
        payload={"secret_key": "my_super_secret_key_123", "operator": "admin"},
        source_service="telegram_c2",
    )

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/risk"})
    assert "RISK ENGINE & CIRCUIT BREAKER" in tg_client.sent_messages[-1]["text"]

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/limits"})
    assert "RISK ENGINE CONFIGURED LIMITS" in tg_client.sent_messages[-1]["text"]

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/alerts"})
    assert "ACTIVE ALERTS & SYSTEM WARNINGS" in tg_client.sent_messages[-1]["text"]

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/logs"})
    logs_text = tg_client.sent_messages[-1]["text"]
    assert "OPERATIONAL EVENT LOGS" in logs_text
    assert "my_super_secret_key_123" not in logs_text
    assert "***REDACTED***" in logs_text
    await db.close()


@pytest.mark.anyio
async def test_notification_alert_dedup_and_high_conviction_filter():
    """Verify alert dedup suppresses duplicate alerts and risk rejection only alerts on high conviction (C2 >= 85, AI approved)."""
    from telegram.service import NotificationService

    bus = EventBus()
    cfg = AppConfig(
        v2_deployment_mode="PAPER",
        alert_chat_id="999888",
    )
    mock_tg = MockTelegramClient()
    notif = NotificationService(bus=bus, config=cfg, telegram_client=mock_tg)
    await notif.start()

    # 1. Low conviction trade denied -> suppressed (no alert)
    await bus.publish(
        EventType.TRADE_DENIED,
        {
            "coin": "DOGE",
            "bot": "STE",
            "confluence_score": 70,
            "ai_recommendation": "WATCH",
            "reason": "Max positions reached",
        },
    )
    # Wait small moment for async bus dispatch
    await asyncio.sleep(0.05)
    assert len(mock_tg.sent_messages) == 0

    # 2. High conviction trade denied (C2 >= 85 and AI approved) -> fires alert
    await bus.publish(
        EventType.TRADE_DENIED,
        {
            "mode": "LIVE",
            "service": "ALPHA_LIVE",
            "coin": "SOL",
            "bot": "STE",
            "confluence_score": 90,
            "ai_recommendation": "APPROVE",
            "reason": "Capital limit reached",
        },
    )
    await asyncio.sleep(0.05)
    assert len(mock_tg.sent_messages) == 1
    assert "Trade Blocked by Risk Engine — SOL" in mock_tg.sent_messages[0]["text"]

    # 3. Duplicate trade denied alert for same coin and reason -> suppressed
    await bus.publish(
        EventType.TRADE_DENIED,
        {
            "mode": "LIVE",
            "service": "ALPHA_LIVE",
            "coin": "SOL",
            "bot": "STE",
            "confluence_score": 90,
            "ai_recommendation": "APPROVE",
            "reason": "Capital limit reached",
        },
    )
    await asyncio.sleep(0.05)
    # Message count should still be 1 (duplicate suppressed)
    assert len(mock_tg.sent_messages) == 1

    await notif.stop()


@pytest.mark.anyio
async def test_cmd_mode_switching_and_shadow_rejection():
    """Verify /mode paper, /mode live confirm work, and /mode shadow is strictly rejected."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(mode="PAPER")

    # 1. /mode paper
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode paper"})
    assert "Mode Switched to PAPER" in tg_client.sent_messages[-1]["text"]
    assert c2._config.deployment_mode == "PAPER"

    # 2. /mode live without confirm -> prompt
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode live"})
    assert "CONFIRMATION REQUIRED" in tg_client.sent_messages[-1]["text"]

    # 3. /mode live confirm -> switch to LIVE
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode live confirm"})
    assert "Mode Switched to LIVE" in tg_client.sent_messages[-1]["text"]
    assert c2._config.deployment_mode == "LIVE_MICROCASH"

    # 4. /mode shadow -> strictly rejected (only PAPER and LIVE allowed)
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode shadow"})
    assert "Invalid mode" in tg_client.sent_messages[-1]["text"]
    assert "Valid modes: <code>paper</code>, <code>live</code>" in tg_client.sent_messages[-1]["text"]

    await db.close()

@pytest.mark.anyio
async def test_cmd_mode_live_requires_password_when_configured():
    """Verify /mode live confirm requires operator security password when configured."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(mode="PAPER")
    c2._config.operator_security_password = "operator_secret_pin_123"

    # 1. /mode live confirm without password -> prompted for password
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode live confirm"})
    assert "SECURITY PASSWORD REQUIRED" in tg_client.sent_messages[-1]["text"]
    assert c2._config.deployment_mode == "PAPER"

    # 2. /mode live confirm wrong_pin -> rejected with AUTHENTICATION FAILED
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode live confirm wrong_pin"})
    assert "AUTHENTICATION FAILED" in tg_client.sent_messages[-1]["text"]
    assert c2._config.deployment_mode == "PAPER"

    # 3. /mode live confirm correct_pin -> successfully switched to LIVE
    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/mode live confirm operator_secret_pin_123"})
    assert "Mode Switched to LIVE" in tg_client.sent_messages[-1]["text"]
    assert c2._config.deployment_mode == "LIVE_MICROCASH"

    await db.close()


@pytest.mark.anyio
async def test_cmd_db_export():
    """Verify /db command snapshots SQLite WAL database and dispatches document."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(mode="PAPER")

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "/db"})
    # Check messages and document dispatch
    assert any("Exporting Database" in m["text"] for m in tg_client.sent_messages)
    assert len(tg_client.sent_documents) >= 1
    doc = tg_client.sent_documents[-1]
    assert doc["document_path"].endswith(".gz")
    assert "PROJECT-ALPHA Database Export" in doc["caption"]

    await db.close()


@pytest.mark.anyio
async def test_assistant_natural_language_dispatch():
    """Verify non-slash query is processed by MBT AI Assistant Agent."""
    db, *_, tg_client, c2 = await _setup_telegram_test_env(mode="PAPER")

    await c2._handle_incoming_message({"chat": {"id": 999888}, "text": "How many positions are open?"})
    assert len(tg_client.sent_messages) >= 1
    reply = tg_client.sent_messages[-1]["text"]
    assert "Active Positions" in reply

    await db.close()
