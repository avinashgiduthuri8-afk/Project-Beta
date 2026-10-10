"""
tests/test_scanner_historical_cutoff.py
Regression: poll(max_timestamp_ms=...) must propagate the historical cut-off to
native candidate generation (prevents look-ahead bias in replays/backtests).
"""

from unittest.mock import MagicMock

import pytest

from core.config import AppConfig
from scanner.service import ScannerService


def _scanner() -> ScannerService:
    return ScannerService(
        bus=MagicMock(),
        signal_repo=MagicMock(),
        event_log_repo=MagicMock(),
        config=AppConfig(),
        calibration_worker=MagicMock(),
    )


@pytest.mark.anyio
async def test_fetch_v1_signals_propagates_cutoff_to_native_generator():
    scanner = _scanner()
    seen = []

    async def fake_native(max_timestamp_ms=None):
        seen.append(max_timestamp_ms)
        return []

    scanner._generate_native_candidates = fake_native
    await scanner._fetch_v1_signals(max_timestamp_ms=1790575200000)
    await scanner._fetch_v1_signals()
    assert seen == [1790575200000, None]


@pytest.mark.anyio
async def test_override_without_cutoff_param_still_supported():
    scanner = _scanner()
    calls = []

    async def legacy_override():
        calls.append("legacy")
        return [{"coin": "BTC"}]

    scanner._fetch_candidate_signals = legacy_override
    out = await scanner._fetch_v1_signals(max_timestamp_ms=123)
    assert out == [{"coin": "BTC"}] and calls == ["legacy"]


@pytest.mark.anyio
async def test_override_with_cutoff_param_receives_it():
    scanner = _scanner()
    seen = []

    async def override(max_timestamp_ms=None):
        seen.append(max_timestamp_ms)
        return []

    scanner._fetch_candidate_signals = override
    await scanner._fetch_v1_signals(max_timestamp_ms=456)
    assert seen == [456]
