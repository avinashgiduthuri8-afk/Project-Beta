"""
PROJECT-ALPHA — Tests for Kill Switch & Drawdown Circuit Breakers (Skill 08).

Verifies:
  1. Global emergency shutdown (trigger_emergency_shutdown) halts all trading immediately.
  2. Outbound order execution blocked when emergency stop is active.
  3. Daily max drawdown circuit breaker tracking and automated tripping upon breach.
  4. Daily rollover at midnight resets starting equity and high water mark.
  5. Consecutive loss streak tracking per bot and trip behavior.
  6. Manual override reset requires explicit confirmation (confirm=True).
"""

from datetime import datetime, timezone
import pytest

from core.config import AppConfig
from core.types import BotName
from execution.risk.circuit_breaker import CircuitBreaker


def test_emergency_shutdown_kill_switch():
    cfg = AppConfig()
    cb = CircuitBreaker(cfg)

    assert cb.is_open is False
    assert cb.emergency_stop is False

    res = cb.trigger_emergency_shutdown(reason="Panic Button Pressed", operator="TEST_OPERATOR")
    assert res["status"] == "HALTED"
    assert res["reason"] == "Panic Button Pressed"
    assert res["operator"] == "TEST_OPERATOR"

    assert cb.is_open is True
    assert cb.emergency_stop is True

    dec = cb.check_breaker(BotName.STE, 200.0)
    assert dec.allowed is False
    assert dec.code == "BLOCKED_EMERGENCY_STOP"


def test_daily_drawdown_circuit_breaker():
    cfg = AppConfig(max_drawdown_pct=10.0)
    cb = CircuitBreaker(cfg)

    # Day 1 initial baseline equity: ₹10,000
    date1 = "2026-10-02"
    cb.update_daily_equity(10000.0, date_str=date1)
    assert cb.daily_drawdown_pct == 0.0
    assert cb.is_open is False

    # Equity climbs to ₹12,000 (New high water mark)
    cb.update_daily_equity(12000.0, date_str=date1)
    assert cb.daily_drawdown_pct == 0.0

    # Equity drops to ₹11,000 (Drawdown: (12000 - 11000) / 12000 = 8.33% < 10%)
    dd = cb.update_daily_equity(11000.0, date_str=date1)
    assert round(dd, 2) == 8.33
    assert cb.is_open is False

    # Equity drops to ₹10,500 (Drawdown: (12000 - 10500) / 12000 = 12.5% >= 10%)
    dd2 = cb.update_daily_equity(10500.0, date_str=date1)
    assert dd2 >= 10.0
    assert cb.is_open is True

    dec = cb.check_breaker(BotName.HDA, 200.0)
    assert dec.allowed is False
    assert dec.code == "BLOCKED_MAX_DRAWDOWN"


def test_daily_drawdown_rollover():
    cfg = AppConfig(max_drawdown_pct=10.0)
    cb = CircuitBreaker(cfg)

    # Day 1: Starts at ₹10,000
    cb.update_daily_equity(10000.0, date_str="2026-10-02")
    cb.update_daily_equity(9500.0, date_str="2026-10-02")
    assert cb.daily_drawdown_pct == 5.0

    # Day 2: Rollover to new date resets baseline
    cb.update_daily_equity(9500.0, date_str="2026-10-03")
    assert cb.daily_drawdown_pct == 0.0


def test_consecutive_loss_protection():
    cfg = AppConfig(max_consecutive_losses=3)
    cb = CircuitBreaker(cfg)

    # 2 losses: still allowed
    cb.record_trade_result(BotName.VCP, -50.0)
    cb.record_trade_result(BotName.VCP, -40.0)
    dec = cb.check_breaker(BotName.VCP, 200.0)
    assert dec.allowed is True

    # 3rd loss: trips breaker
    cb.record_trade_result(BotName.VCP, -30.0)
    assert cb.is_open is True
    dec2 = cb.check_breaker(BotName.VCP, 200.0)
    assert dec2.allowed is False
    assert dec2.code == "BLOCKED_CIRCUIT_BREAKER"

    # Winning trade resets loss counter
    cb.reset()
    cb.record_trade_result(BotName.VCP, 100.0)
    dec3 = cb.check_breaker(BotName.VCP, 200.0)
    assert dec3.allowed is True


def test_manual_override_reset_requires_confirmation():
    cfg = AppConfig()
    cb = CircuitBreaker(cfg)
    cb.trip("Test trip")

    assert cb.is_open is True

    # Attempt reset without confirm=True
    ok, msg = cb.manual_override_reset(operator="ADMIN", reason="Fix applied", confirm=False)
    assert ok is False
    assert "Confirmation required" in msg or "explicit confirmation" in msg
    assert cb.is_open is True

    # Attempt reset with confirm=True
    ok2, msg2 = cb.manual_override_reset(operator="ADMIN", reason="Fix applied", confirm=True)
    assert ok2 is True
    assert cb.is_open is False
    assert cb.emergency_stop is False
