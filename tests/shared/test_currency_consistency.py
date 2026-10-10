"""
Unit tests for Project Alpha currency consistency across Telegram formatters,
aggregators, and interactive interfaces.
"""

from datetime import datetime, timezone
import pytest

from background.portfolio.aggregator import PortfolioAggregator
from core.types import BotMode, BotName, ExitReason, Position, PositionStatus, Trade
from telegram.formatters import (
    format_position_opened_alert,
    format_telegram_orders,
    format_telegram_positions,
    format_telegram_trades,
    format_telegram_scan,
    format_telegram_signal_detail,
)


def test_format_orders_usdt_and_inr():
    orders = [
        {
            "coin": "PARTI",
            "pair": "PARTI_USDT",
            "side": "BUY",
            "qty": 14.885496,
            "price": 0.1834,
            "mode": "PAPER",
            "status": "OPEN",
            "exchange_order_id": "LOCAL_PAPER",
        },
        {
            "coin": "BTC",
            "pair": "BTCINR",
            "side": "BUY",
            "qty": 0.0001,
            "price": 6000000.0,
            "mode": "PAPER",
            "status": "OPEN",
            "exchange_order_id": "LOCAL_PAPER",
        },
    ]
    text = format_telegram_orders(orders, mode="PAPER")
    assert "$0.18" in text
    assert "₹6000000.00" in text
    assert "@ ₹0.18" not in text


def test_format_position_opened_alert_dual_notional():
    # USDT position
    usdt_payload = {
        "coin": "PARTI",
        "pair": "PARTI_USDT",
        "bot": "STE",
        "entry_price": 0.1834,
        "qty": 14.885496,
        "amount": 2.73,
        "stop_loss": 0.1742,
        "take_profit": 0.2017,
        "usdt_inr_rate": 91.50,
    }
    msg_usdt = format_position_opened_alert(usdt_payload)
    assert "$2.73 (₹249.80)" in msg_usdt or "$2.73 (₹249.79)" in msg_usdt or "$2.73 (₹25" in msg_usdt
    assert "$0.1834" in msg_usdt
    assert "TP: $0.2017" in msg_usdt or "$0.2017" in msg_usdt
    assert "SL: $0.1742" in msg_usdt or "$0.1742" in msg_usdt

    # INR position
    inr_payload = {
        "coin": "BTC",
        "pair": "BTCINR",
        "bot": "STE",
        "entry_price": 6000000.0,
        "qty": 0.0001,
        "amount": 600.0,
        "stop_loss": 5800000.0,
        "take_profit": 6300000.0,
    }
    msg_inr = format_position_opened_alert(inr_payload)
    assert "₹600.00" in msg_inr
    assert "₹6000000.00" in msg_inr


def test_format_positions_dual_capital():
    positions = [
        {
            "coin": "PARTI",
            "pair": "PARTI_USDT",
            "bot": "STE",
            "qty": 14.885496,
            "entry_price": 0.1834,
            "current_price": 0.1900,
            "unrealised_pnl": 0.0982,
            "stop_loss": 0.1742,
            "take_profit": 0.2017,
            "status": "OPEN",
            "usdt_inr_rate": 91.50,
        }
    ]
    text = format_telegram_positions(positions)
    assert "PARTI_USDT" in text
    assert "$2.73 (₹249.80)" in text or "$2.73 (₹250.00)" in text or "Capital: $2.73" in text
    assert "Entry: $0.18" in text
    assert "Unrealized P&L: <code>+$0.10 (+3.60%)</code>" in text or "+$0.10" in text


def test_format_trades_currency_symbol():
    trades = [
        {
            "coin": "PARTI",
            "pair": "PARTI_USDT",
            "bot": "STE",
            "pnl": 0.12,
            "pnl_pct": 4.40,
            "exit_reason": "TAKE_PROFIT",
        },
        {
            "coin": "BTC",
            "pair": "BTCINR",
            "bot": "STE",
            "pnl": 50.0,
            "pnl_pct": 2.5,
            "exit_reason": "TAKE_PROFIT",
        },
    ]
    text = format_telegram_trades(trades)
    assert "+$0.12 (+4.40%)" in text
    assert "+₹50.00 (+2.50%)" in text


def test_format_scan_and_signal_inspector():
    scan_signals = [
        {
            "coin": "PARTI",
            "pair": "PARTI_USDT",
            "score": 92,
            "price": 0.1834,
            "direction": "BUY",
        }
    ]
    scan_text = format_telegram_scan({"signals": scan_signals, "last_scan_at": "NOW", "evaluated_count": 10, "candidate_count": 2})
    assert "@ $0.18" in scan_text

    detail_payload = {
        "pair": "PARTI_USDT",
        "price": 0.1834,
        "score": 92,
        "bot": "STE",
        "status": "EVALUATED",
    }
    detail_text = format_telegram_signal_detail(detail_payload, "PARTI_USDT")
    assert "$0.1834" in detail_text


def test_portfolio_aggregator_fx_normalization():
    now = datetime.now(timezone.utc)
    pos_usdt = Position(
        id="pos-1",
        bot=BotName.STE,
        coin="PARTI",
        pair="PARTI_USDT",
        qty=10.0,
        entry_price=1.0,
        entry_time=now,
        mode=BotMode.PAPER,
        current_price=1.1,
        unrealised_pnl=1.0,  # +1.0 USDT
    )
    # Deployed: 10 * 1.0 * 91.50 = 915.0 INR
    trade_usdt = Trade(
        id="trade-1",
        position_id="pos-0",
        bot=BotName.STE,
        coin="PARTI",
        pair="PARTI_USDT",
        entry_price=1.0,
        exit_price=1.2,
        qty=10.0,
        pnl=2.0,  # +2.0 USDT
        pnl_pct=20.0,
        entry_time=now,
        exit_time=now,
        exit_reason=ExitReason.TAKE_PROFIT,
        mode=BotMode.PAPER,
    )

    snap = PortfolioAggregator.aggregate(
        positions=[pos_usdt],
        closed_trades=[trade_usdt],
        base_cash=10000.0,
    )

    # 1.0 USDT * 91.50 = 91.50 INR
    assert snap.total_unrealised_pnl == 91.50
    # 2.0 USDT * 91.50 = 183.00 INR
    assert snap.total_realised_pnl == 183.00
    # Deployed = 915.0 INR
    assert snap.total_deployed == 915.00
    # Daily PnL = Realised today (183.0) + Unrealised (91.5) = 274.50 INR
    assert snap.daily_pnl == 274.50
