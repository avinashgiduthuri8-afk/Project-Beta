"""Unit tests for Step 5: RMS Capital Guard and Single-Asset Invariant."""

import pytest
from v2.services.risk_service.capital_guard import RMSCapitalGuard, CapitalGuardConfig


def test_single_asset_lock_invariant():
    guard = RMSCapitalGuard(CapitalGuardConfig(single_asset_lock=True))
    active_positions = {"RELIANCE": {"qty": 10, "entry_price": 2400.0, "notional": 24000.0}}

    # Attempt to open second position on RELIANCE -> Blocked by Single-Asset Lock
    approved, reason = guard.validate_new_trade(
        symbol="RELIANCE",
        notional_cost=24000.0,
        current_equity=1000000.0,
        current_cash=900000.0,
        active_positions=active_positions,
    )
    assert approved is False
    assert "Single-Asset Lock Violation" in reason

    # Trade on different ticker TCS -> Approved
    approved_tcs, reason_tcs = guard.validate_new_trade(
        symbol="TCS",
        notional_cost=30000.0,
        current_equity=1000000.0,
        current_cash=900000.0,
        active_positions=active_positions,
    )
    assert approved_tcs is True


def test_daily_drawdown_circuit_breaker():
    guard = RMSCapitalGuard(CapitalGuardConfig(max_daily_loss_pct=0.03))
    guard.set_starting_equity(1000000.0)

    # 3.5% Loss -> Circuit breaker trips
    approved, reason = guard.validate_new_trade(
        symbol="INFY",
        notional_cost=20000.0,
        current_equity=965000.0,
        current_cash=800000.0,
        active_positions={},
        realized_daily_pnl=-35000.0,
    )
    assert approved is False
    assert "circuit breaker" in reason.lower()
    assert guard.circuit_breaker_tripped is True

