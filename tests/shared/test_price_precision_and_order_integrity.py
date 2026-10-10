"""
Unit and Regression Tests for PROJECT-ALPHA Issue #1:
Sub-₹1 price/decimal corruption, zero-price order prevention, quote-currency isolation,
TP/SL integrity, runtime price-jump guards, and P&L safety.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.bus.event_bus import EventBus
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus, Trade
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from execution.service import TradingService
from execution.trading.precision_rules import (
    infer_price_decimals,
    normalize_price,
    normalize_qty,
    round_price,
    round_qty_up,
    validate_order_notional,
    validate_trade_parameters,
)
from execution.trading.subaccount_manager import (
    CoinDCXSubAccountClient,
    SubAccountConfig,
)

# ── 1. Canonical Normalization Tests ──────────────────────────────────────────


def test_canonical_price_normalization():
    """Verify normalize_price accepts valid representations and strictly rejects invalid values."""
    # Valid numeric representations
    assert normalize_price(0.0034) == 0.0034
    assert normalize_price("0.0034") == 0.0034
    assert normalize_price(" 0.16 ") == 0.16
    assert normalize_price(8200000) == 8200000.0
    assert normalize_price("8,200,000.50") == 8200000.50

    # None, zero, negative, NaN, Inf, and malformed strings MUST raise ValueError
    with pytest.raises(ValueError, match="cannot be None"):
        normalize_price(None)
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price(0)
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price(0.0)
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price("0.0")
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price(-0.16)
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price("-100")
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price(float("nan"))
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price(float("inf"))
    with pytest.raises(ValueError, match="Invalid price value"):
        normalize_price(float("-inf"))
    with pytest.raises(ValueError, match="Cannot parse price"):
        normalize_price("malformed_price")
    with pytest.raises(ValueError, match="Cannot parse price"):
        normalize_price("12.34.56")


def test_canonical_quantity_normalization():
    """Verify normalize_qty accepts valid quantities and rejects non-positive, NaN, Inf, None."""
    assert normalize_qty(10) == 10.0
    assert normalize_qty("250.5") == 250.5
    assert normalize_qty(0.0001) == 0.0001

    with pytest.raises(ValueError, match="cannot be None"):
        normalize_qty(None)
    with pytest.raises(ValueError, match="Invalid quantity value"):
        normalize_qty(0)
    with pytest.raises(ValueError, match="Invalid quantity value"):
        normalize_qty(-1)
    with pytest.raises(ValueError, match="Invalid quantity value"):
        normalize_qty(float("nan"))
    with pytest.raises(ValueError, match="Invalid quantity value"):
        normalize_qty(float("inf"))
    with pytest.raises(ValueError, match="Cannot parse quantity"):
        normalize_qty("zero_qty")


# ── 2. Price Precision & Non-Zero Rounding Tests ──────────────────────────────


def test_dynamic_precision_inference():
    """Verify precision scaling for standard, fractional, and sub-₹1 assets."""
    assert infer_price_decimals(8200000.0) == 2  # BTC
    assert infer_price_decimals(12500.0) == 2  # SOL
    assert infer_price_decimals(16.50) == 4  # DOGE
    assert infer_price_decimals(0.82) == 6  # SUI
    assert infer_price_decimals(0.16) == 6  # ENA
    assert infer_price_decimals(0.0034) == 8  # BONK
    assert infer_price_decimals(0.001845) == 8  # SHIB
    assert infer_price_decimals(0.000008) == 10  # PEPE
    assert infer_price_decimals(0.0000005) == 12  # Extreme micro-asset


def test_positive_prices_never_round_to_zero():
    """
    Harden round_price so that any strictly positive price NEVER rounds to 0.0,
    even for unlisted coins where DEFAULT_SPEC is used.
    """
    assert round_price("BONK/INR", 0.0034) == 0.0034
    assert round_price("SHIB/INR", 0.001845) == 0.001845
    assert round_price("PEPE/USDT", 0.000008) == 0.000008
    assert round_price("UNLISTED/INR", 0.0000045) == 0.0000045
    assert round_price("MICRO/INR", 0.00000012) == 0.00000012

    # Negative / zero / nan / inf returns 0.0 as safe defense
    assert round_price("BTC/INR", 0.0) == 0.0
    assert round_price("BTC/INR", -10.0) == 0.0
    assert round_price("BTC/INR", float("nan")) == 0.0


def test_bonk_sub_1_rupee_regression():
    """Test BONK ₹0.0034: preserves magnitude, calculates qty >= 200 notional, no zero price."""
    pair = "BONK/INR"
    entry = 0.0034
    rounded_entry = round_price(pair, entry)
    assert rounded_entry == 0.0034

    qty = round_qty_up(pair, 200.0 / rounded_entry)
    notional = rounded_entry * qty
    assert notional >= 200.0
    assert validate_order_notional(pair, rounded_entry, qty)

    # Simulated 5% take-profit exit at 0.00357
    exit_px = 0.00357
    gross_pnl = (exit_px - rounded_entry) * qty
    gross_pnl_pct = ((exit_px - rounded_entry) / rounded_entry) * 100.0
    assert math.isclose(gross_pnl_pct, 5.0, rel_tol=1e-2)
    assert gross_pnl > 0


def test_shib_sub_1_rupee_regression():
    """Test SHIB ₹0.0018: verifies tick and lot preservation without precision loss."""
    pair = "SHIB/INR"
    entry = 0.001845
    rounded_entry = round_price(pair, entry)
    assert rounded_entry == 0.001845

    qty = round_qty_up(pair, 200.0 / rounded_entry)
    assert qty >= 1000.0
    assert rounded_entry * qty >= 200.0


def test_pepe_sub_1_rupee_regression():
    """Test PEPE sub-₹1 (0.000008): preserves 8-10 decimals, cannot floor to 0.0."""
    pair = "PEPE/USDT"
    entry = 0.00000823
    rounded = round_price(pair, entry)
    assert rounded == 0.00000823
    assert rounded > 0.0


# ── 3. Pre-Execution Parameter Validation ──────────────────────────────────────


def test_validate_trade_parameters():
    """Test validate_trade_parameters gates on price, qty, notional, and SL/TP bounds."""
    pair = "BTC/INR"
    price = 8200000.0
    qty = 0.00003  # notional ~ 246 INR

    # Valid parameters
    valid, err = validate_trade_parameters(
        pair=pair,
        price=price,
        qty=qty,
        stop_loss=8000000.0,
        take_profit=8500000.0,
        min_notional=200.0,
    )
    assert valid is True
    assert err is None

    # Zero / Negative Price
    v, e = validate_trade_parameters(pair=pair, price=0.0, qty=qty)
    assert v is False and "Invalid entry price" in e
    v, e = validate_trade_parameters(pair=pair, price=-100.0, qty=qty)
    assert v is False and "Invalid entry price" in e

    # Zero / Negative Quantity
    v, e = validate_trade_parameters(pair=pair, price=price, qty=0.0)
    assert v is False and "Invalid quantity" in e
    v, e = validate_trade_parameters(pair=pair, price=price, qty=-0.1)
    assert v is False and "Invalid quantity" in e

    # Notional below minimum (e.g. ₹50 < ₹200)
    v, e = validate_trade_parameters(
        pair=pair, price=price, qty=0.000005, min_notional=200.0
    )
    assert v is False and "below minimum" in e

    # Inverted Long SL (SL >= entry)
    v, e = validate_trade_parameters(
        pair=pair, price=price, qty=qty, stop_loss=8300000.0, take_profit=8500000.0
    )
    assert v is False and "strictly below" in e

    # Inverted Long TP (TP <= entry)
    v, e = validate_trade_parameters(
        pair=pair, price=price, qty=qty, stop_loss=8000000.0, take_profit=8100000.0
    )
    assert v is False and "strictly above" in e

    # Extreme SL/TP ratio bounds (e.g. SL at 10x or 0.01x entry)
    v, e = validate_trade_parameters(
        pair=pair, price=100.0, qty=3.0, stop_loss=5.0, take_profit=110.0
    )
    assert v is False and "magnitude is inconsistent" in e
    v, e = validate_trade_parameters(
        pair=pair, price=100.0, qty=3.0, stop_loss=95.0, take_profit=600.0
    )
    assert v is False and "magnitude is inconsistent" in e


# ── 4. Quote-Currency Isolation & Cross-Quote Protection ───────────────────────


@pytest.mark.asyncio
async def test_ena_inr_vs_ena_usdt_quote_isolation():
    """
    Test ENA/INR (₹16.05) vs ENA/USDT ($0.16):
    Verify check_open_position_exits uses exact quote currency and NEVER swaps quotes.
    """
    db = Database(":memory:")
    await db.open()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_log = EventLogRepository(db.connection)
    bus = EventBus()

    cfg = MagicMock()
    cfg.order_size_inr = 200.0
    cfg.enforce_single_coin_lock = False
    cfg.v2_deployment_mode = "PAPER"
    cfg.v2_trading_enabled = True

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log,
        config=cfg,
    )

    now = datetime.now(timezone.utc)

    # 1. Open USDT position: entry $0.16, TP $0.17
    pos_usdt = Position(
        id=str(uuid.uuid4()),
        bot=BotName.STE,
        coin="ENA",
        pair="ENA/USDT",
        qty=1250.0,
        entry_price=0.16,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=0.16,
        unrealised_pnl=0.0,
        stop_loss=0.156,
        take_profit=0.17,
    )
    await pos_repo.insert(pos_usdt)

    # 2. Open INR position: entry ₹16.05, TP ₹17.00
    pos_inr = Position(
        id=str(uuid.uuid4()),
        bot=BotName.VCP,
        coin="ENA",
        pair="ENA/INR",
        qty=15.0,
        entry_price=16.05,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=16.05,
        unrealised_pnl=0.0,
        stop_loss=15.50,
        take_profit=17.00,
    )
    await pos_repo.insert(pos_inr)

    # Ticker dictionary containing BOTH markets
    current_prices = {
        "ENA/USDT": 0.162,
        "ENAUSDT": 0.162,
        "ENA/INR": 16.15,
        "ENAINR": 16.15,
    }

    # Execute exit checks
    closed = await service.check_open_position_exits(current_prices)

    # Neither position should trigger false TP because prices are within normal fluctuations
    assert len(closed) == 0

    # Verify positions kept their respective quote currency prices
    db_usdt = await pos_repo.get_by_id(pos_usdt.id)
    db_inr = await pos_repo.get_by_id(pos_inr.id)

    assert math.isclose(db_usdt.current_price, 0.162, rel_tol=1e-3)
    assert math.isclose(db_inr.current_price, 16.15, rel_tol=1e-3)

    await db.close()


@pytest.mark.asyncio
async def test_sui_inr_vs_sui_usdt_quote_isolation():
    """
    Test SUI/INR (₹82.00) vs SUI/USDT ($0.82):
    Verify that an INR price spike in the ticker map NEVER contaminates SUI/USDT.
    """
    db = Database(":memory:")
    await db.open()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    event_log = EventLogRepository(db.connection)
    bus = EventBus()

    cfg = MagicMock()
    cfg.order_size_inr = 200.0
    cfg.enforce_single_coin_lock = False
    cfg.v2_deployment_mode = "PAPER"
    cfg.v2_trading_enabled = True

    service = TradingService(
        bus=bus,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        event_log_repo=event_log,
        config=cfg,
    )

    now = datetime.now(timezone.utc)
    pos_usdt = Position(
        id=str(uuid.uuid4()),
        bot=BotName.VCP,
        coin="SUI",
        pair="SUI/USDT",
        qty=250.0,
        entry_price=0.82,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=0.82,
        unrealised_pnl=0.0,
        stop_loss=0.80,
        take_profit=0.86,
    )
    await pos_repo.insert(pos_usdt)

    # Ticker map where SUI/INR is 82.00 but SUI/USDT is absent
    current_prices = {
        "SUI/INR": 82.00,
        "SUIINR": 82.00,
    }

    # SUI/USDT should NOT match SUI/INR
    closed = await service.check_open_position_exits(current_prices)
    assert len(closed) == 0

    # Current price must NOT be contaminated by 82.00
    pos_after = await pos_repo.get_by_id(pos_usdt.id)
    assert pos_after.current_price == 0.82

    await db.close()


# ── 5. Runtime Price-Jump Guard (100x Jump & 0.01x Collapse Protection) ───────


@pytest.mark.asyncio
async def test_runtime_price_jump_guard():
    """
    Test Runtime Price-Jump Guard:
    Reject ratios outside 0.10 <= current_price / entry_price <= 10.0 across
    register_position, update_mark_price, close_position, and check_open_position_exits.
    """
    db = Database(":memory:")
    await db.open()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    bus = EventBus()
    pos_mgr = PositionManager(position_repo=pos_repo, trade_repo=trade_repo, bus=bus)

    # 1. Test register_position with 100x jump on initial current_price
    with pytest.raises(ValueError, match="Abnormal price ratio"):
        await pos_mgr.register_position(
            bot=BotName.STE,
            coin="ENA",
            pair="ENA/USDT",
            entry_price=0.16,
            qty=1250.0,
            current_price=16.05,  # 100x jump
        )

    # 2. Register valid position
    pos = await pos_mgr.register_position(
        bot=BotName.STE,
        coin="ENA",
        pair="ENA/USDT",
        entry_price=0.16,
        qty=1250.0,
        stop_loss=0.15,
        take_profit=0.17,
        current_price=0.16,
    )
    assert pos.entry_price == 0.16

    # 3. Test update_mark_price with 100x jump (0.16 -> 16.0)
    unrealised_jump = await pos_mgr.update_mark_price(pos, 16.0)
    assert unrealised_jump == 0.0  # Skipped, preserved previous P&L
    assert pos.current_price == 0.16

    # 4. Test update_mark_price with 0.01x collapse (0.16 -> 0.001)
    unrealised_collapse = await pos_mgr.update_mark_price(pos, 0.001)
    assert unrealised_collapse == 0.0  # Skipped
    assert pos.current_price == 0.16

    # 5. Test update_mark_price with normal price movement (0.16 -> 0.165)
    unrealised_normal = await pos_mgr.update_mark_price(pos, 0.165)
    assert unrealised_normal > 0.0
    assert pos.current_price == 0.165

    # 6. Test close_position with 100x jump (rejected)
    c_pos, c_trade = await pos_mgr.close_position(pos.id, exit_price=16.0)
    assert c_pos is None
    assert c_trade is None

    # 7. Test close_position with 0.01x collapse (rejected)
    c_pos, c_trade = await pos_mgr.close_position(pos.id, exit_price=0.001)
    assert c_pos is None
    assert c_trade is None

    # 8. Test close_position with normal exit price (accepted)
    c_pos, c_trade = await pos_mgr.close_position(pos.id, exit_price=0.168)
    assert c_pos is not None
    assert c_trade is not None
    assert c_trade.entry_price == 0.16
    assert c_trade.exit_price == 0.168
    assert c_trade.pnl > 0

    await db.close()


# ── 6. Order Rejection Before Dispatch (Silent Fallback Elimination) ──────────


@pytest.mark.asyncio
async def test_order_rejection_before_dispatch_in_auto_trader():
    """Verify AutoTradeRouter rejects signals with missing/zero/invalid prices without dispatching."""
    bus = EventBus()
    sub_mgr = MagicMock()
    mock_client = MagicMock()
    sub_mgr.get_client.return_value = mock_client

    router = AutoTradeRouter(bus=bus, subaccount_manager=sub_mgr, dry_run=True)

    # Missing price
    res_none = await router.handle_signal(
        {"coin": "BTC", "pair": "BTC/INR", "price": None}
    )
    assert res_none["success"] is False
    assert "invalid price" in res_none["message"].lower()

    # Zero price
    res_zero = await router.handle_signal(
        {"coin": "BONK", "pair": "BONK/INR", "price": 0.0}
    )
    assert res_zero["success"] is False
    assert "invalid price" in res_zero["message"].lower()

    # Negative price
    res_neg = await router.handle_signal(
        {"coin": "ETH", "pair": "ETH/INR", "price": -50.0}
    )
    assert res_neg["success"] is False

    # NaN price
    res_nan = await router.handle_signal(
        {"coin": "ETH", "pair": "ETH/INR", "price": float("nan")}
    )
    assert res_nan["success"] is False

    # Mock client must NEVER have been called to place an order
    mock_client.place_order.assert_not_called()
    mock_client.place_order_async.assert_not_called()


def test_subaccount_manager_order_layer_normalization():
    """Verify CoinDCXSubAccountClient normalizes inputs and applies dynamic pair precision."""
    config = SubAccountConfig(
        bot_name=BotName.STE,
        subaccount_id="TEST_SUB",
        api_key="test_key",
        api_secret="test_sec",
        allocated_wallet_inr=10000.0,
    )
    client = CoinDCXSubAccountClient(config=config)

    # 1. Invalid price rejection
    res_zero = client.place_order(pair="BONK/INR", side="BUY", price=0.0, qty=1000.0)
    assert res_zero["success"] is False
    assert res_zero["error"] == "INVALID_PRICE_OR_QUANTITY"

    # 2. Invalid qty rejection
    res_qty = client.place_order(pair="BTC/INR", side="BUY", price=8200000.0, qty=0.0)
    assert res_qty["success"] is False
    assert res_qty["error"] == "INVALID_PRICE_OR_QUANTITY"

    # 3. Valid micro-order with dynamic precision
    res_valid = client.place_order(
        pair="BONK/INR", side="BUY", price=0.0034, qty=60000.0
    )
    assert res_valid["success"] is True
    assert res_valid["order"]["price"] == 0.0034
    assert res_valid["order"]["qty"] == 60000.0
    assert res_valid["order"]["notional_inr"] >= 200.0


# ── 7. Read-Only Historical Contamination Diagnostic ──────────────────────────


@pytest.mark.asyncio
async def test_historical_contamination_diagnostic_read_only():
    """Verify audit_historical_trades identifies corrupt records without modifying the DB."""
    db = Database(":memory:")
    await db.open()
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)
    now = datetime.now(timezone.utc)

    # 1. Clean position & trade
    pos_id_clean = str(uuid.uuid4())
    pos_clean = Position(
        id=pos_id_clean,
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.0001,
        entry_price=8200000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.CLOSED,
        current_price=8500000.0,
        unrealised_pnl=0.0,
    )
    await pos_repo.insert(pos_clean)

    t_clean = Trade(
        id=str(uuid.uuid4()),
        position_id=pos_id_clean,
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        entry_price=8200000.0,
        exit_price=8500000.0,
        qty=0.0001,
        pnl=30.0,
        pnl_pct=3.65,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )
    await trade_repo.insert(t_clean)

    # 2. Corrupted 100x jump position & trade (ENA ₹0.16 -> ₹16.05)
    pos_id_corrupt = str(uuid.uuid4())
    pos_corrupt = Position(
        id=pos_id_corrupt,
        bot=BotName.STE,
        coin="ENA",
        pair="ENA/INR",
        qty=1250.0,
        entry_price=0.16,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.CLOSED,
        current_price=16.05,
        unrealised_pnl=0.0,
    )
    await pos_repo.insert(pos_corrupt)

    t_corrupt = Trade(
        id=str(uuid.uuid4()),
        position_id=pos_id_corrupt,
        bot=BotName.STE,
        coin="ENA",
        pair="ENA/INR",
        entry_price=0.16,
        exit_price=16.05,
        qty=1250.0,
        pnl=19862.5,
        pnl_pct=9931.25,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )
    await trade_repo.insert(t_corrupt)

    # 3. Run audit diagnostic
    suspicious = await trade_repo.audit_historical_trades()
    assert len(suspicious) == 1
    assert suspicious[0]["trade_id"] == t_corrupt.id
    assert suspicious[0]["status"] == "CORRUPTED_SUSPICIOUS"
    assert any("Abnormal price-ratio jump" in iss for iss in suspicious[0]["issues"])

    # 4. Confirm historical data is strictly preserved and unmodified
    all_trades = await trade_repo.get_by_bot(BotName.STE)
    assert len(all_trades) == 2

    await db.close()
