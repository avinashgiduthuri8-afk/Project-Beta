"""
PROJECT-ALPHA — Tests for Paper/Live Execution Mode Isolation (Skill 14).

Verifies:
  1. Credentials validation (rejects demo keys in live mode, validates paper mode).
  2. Outbound dispatch firewall (strictly blocks outbound exchange orders in paper mode).
  3. Balance access isolation (blocks paper mode from querying live exchange balance).
  4. Order intent validation (blocks LIVE order when deployment mode is PAPER).
  5. Master switch enforcement (blocks LIVE order when trading_enabled is False).
"""

import pytest

from core.config import AppConfig
from core.types import BotMode
from execution.trading.mode_isolation import (
    IsolationViolationType,
    ModeIsolationGuard,
)


def test_credential_isolation_live_vs_paper():
    guard = ModeIsolationGuard()

    # Valid live credentials
    ok, msg = guard.validate_credentials("LIVE", "valid_live_api_key_123", "secret_live_456")
    assert ok is True
    assert msg == "VALID_LIVE_CREDENTIALS"

    # Missing credentials in LIVE mode
    ok, msg = guard.validate_credentials("LIVE", None, None)
    assert ok is False
    assert "Missing" in msg or "requires" in msg

    # Demo/mock key in LIVE mode must be rejected
    ok, msg = guard.validate_credentials("LIVE", "paper_key_demo", "demo_secret")
    assert ok is False
    assert "rejected demo/mock" in msg

    # Paper mode credentials allow mock keys
    ok, msg = guard.validate_credentials("PAPER", "paper_key_demo", "paper_secret_demo")
    assert ok is True


def test_outbound_dispatch_firewall():
    guard = ModeIsolationGuard()

    # Paper mode attempting real outbound network request is strictly blocked
    ok, code = guard.validate_outbound_dispatch("PAPER", is_live_network_call=True)
    assert ok is False
    assert code == IsolationViolationType.LIVE_API_CALLED_IN_PAPER.value

    # Paper mode simulation is allowed
    ok, code = guard.validate_outbound_dispatch("PAPER", is_live_network_call=False)
    assert ok is True

    # Live mode real dispatch is allowed
    ok, code = guard.validate_outbound_dispatch("LIVE", is_live_network_call=True)
    assert ok is True


def test_balance_source_isolation():
    guard = ModeIsolationGuard()

    # Paper mode querying live exchange balance is blocked
    ok, code = guard.validate_balance_access("PAPER", "LIVE_EXCHANGE")
    assert ok is False
    assert code == IsolationViolationType.BALANCE_SOURCE_MISMATCH.value

    # Paper mode accessing virtual balance is allowed
    ok, code = guard.validate_balance_access("PAPER", "PAPER_VIRTUAL")
    assert ok is True

    # Live mode relying on virtual paper balance is blocked
    ok, code = guard.validate_balance_access("LIVE", "PAPER_VIRTUAL")
    assert ok is False
    assert code == IsolationViolationType.BALANCE_SOURCE_MISMATCH.value


def test_order_intent_mode_gate():
    cfg = AppConfig(deployment_mode="PAPER", trading_enabled=False)
    guard = ModeIsolationGuard(config=cfg)

    # Cannot dispatch LIVE order in PAPER deployment
    ok, msg = guard.validate_order_intent(
        deployment_mode="PAPER",
        trading_enabled=False,
        target_mode="LIVE",
    )
    assert ok is False
    assert "Cannot execute LIVE order" in msg

    # LIVE deployment with trading_enabled=False (Kill switch active) blocks LIVE orders
    ok, msg = guard.validate_order_intent(
        deployment_mode="LIVE",
        trading_enabled=False,
        target_mode="LIVE",
    )
    assert ok is False
    assert "Kill Switch Active" in msg

    # LIVE deployment with trading_enabled=True allows LIVE orders
    ok, msg = guard.validate_order_intent(
        deployment_mode="LIVE",
        trading_enabled=True,
        target_mode="LIVE",
    )
    assert ok is True
    assert msg == "ALLOWED"
