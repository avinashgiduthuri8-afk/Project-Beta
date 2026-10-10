"""
PROJECT-ALPHA — Unit Tests for Strategy Decay Monitoring Engine (Skill 23).
"""

from __future__ import annotations

import pytest

from core.types import BotName
from execution.risk.strategy_decay import (
    DecayReport,
    StrategyDecayMonitor,
    StrategyHealthState,
)


def test_strategy_decay_insufficient_samples():
    monitor = StrategyDecayMonitor(min_sample_trades=10)
    # Record 5 trades
    for pnl in [-10.0, 20.0, -15.0, 30.0, -5.0]:
        report = monitor.record_trade(BotName.STE, pnl)

    assert report.total_trades == 5
    assert report.health_state == StrategyHealthState.HEALTHY
    dec = monitor.check_strategy_allowed(BotName.STE)
    assert dec.allowed is True


def test_strategy_decay_healthy_sequence():
    monitor = StrategyDecayMonitor(min_sample_trades=10)
    # Record 10 trades with 70% win rate
    trades = [25.0, 30.0, -10.0, 40.0, 15.0, -12.0, 35.0, 20.0, -8.0, 50.0]
    for pnl in trades:
        report = monitor.record_trade(BotName.HDA, pnl)

    assert report.total_trades == 10
    assert report.win_rate_pct == 70.0
    assert report.expectancy > 15.0
    assert report.health_state == StrategyHealthState.HEALTHY

    dec = monitor.check_strategy_allowed(BotName.HDA)
    assert dec.allowed is True


def test_strategy_decay_negative_expectancy_halts():
    monitor = StrategyDecayMonitor(min_sample_trades=10, min_win_rate_pct=35.0, min_expectancy=0.0)
    # Record 10 trades with 20% win rate and heavy losses
    trades = [-20.0, -25.0, 10.0, -30.0, -15.0, -18.0, -22.0, 12.0, -35.0, -40.0]
    for pnl in trades:
        report = monitor.record_trade(BotName.VCP, pnl)

    assert report.total_trades == 10
    assert report.win_rate_pct == 20.0
    assert report.expectancy < 0.0
    assert report.health_state == StrategyHealthState.DECAYED_HALTED

    dec = monitor.check_strategy_allowed(BotName.VCP)
    assert dec.allowed is False
    assert dec.code == "BLOCKED_STRATEGY_DECAY"
    assert "HALTED due to performance decay" in dec.reason


def test_strategy_decay_reset():
    monitor = StrategyDecayMonitor(min_sample_trades=10)
    trades = [-20.0] * 10
    for pnl in trades:
        monitor.record_trade(BotName.BBS, pnl)

    assert monitor.evaluate_strategy_health(BotName.BBS).health_state == StrategyHealthState.DECAYED_HALTED

    # Manual reset after recalibration
    monitor.reset_strategy(BotName.BBS)
    reset_report = monitor.evaluate_strategy_health(BotName.BBS)
    assert reset_report.total_trades == 0
    assert reset_report.health_state == StrategyHealthState.HEALTHY

