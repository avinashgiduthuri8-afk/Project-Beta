"""
conftest.py — PROJECT-ALPHA test bootstrap.

Sets safe defaults for every environment variable the application reads at
import time so tests never fail due to missing env vars. Uses setdefault so
real environment values are never overwritten.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

# ── Ensure services/ and shared/ packages are on sys.path ─────────────────────
_ROOT = Path(__file__).resolve().parent.parent
for _p in [
    _ROOT,
    _ROOT / "services",
    _ROOT / "shared",
    _ROOT / "shared" / "core",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ── Backward-compatibility shims for relocated modules ─────────────────────────
try:
    import shared.research as _shared_research
    import shared.research.indicators as _indicators
    import shared.research.service as _r_service
    import shared.research.symbol_normalizer as _normalizer
    sys.modules["scanner.research"] = _shared_research
    sys.modules["scanner.research.indicators"] = _indicators
    sys.modules["scanner.research.service"] = _r_service
    sys.modules["scanner.research.symbol_normalizer"] = _normalizer
except Exception:
    pass

# ── Core application secrets ──────────────────────────────────────────────────
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

# ── Trading kill-switches (off by default in tests) ───────────────────────────
os.environ.setdefault("TRADING_ENABLED", "false")
os.environ.setdefault("EMERGENCY_STOP", "false")

# ── Bot modes (paper so risk engine is exercisable) ───────────────────────────
os.environ.setdefault("MTB_BOT_MODE", "PAPER")

# ── Capital / position limits ─────────────────────────────────────────────────
os.environ.setdefault("MTB_TRADE_AMOUNT", "200")
os.environ.setdefault("MTB_CAPITAL_LIMIT", "2000")
os.environ.setdefault("MTB_MAX_POSITIONS", "3")


@pytest.fixture(autouse=True)
def isolate_test_runtime_config(tmp_path, monkeypatch):
    """Ensure tests write runtime overrides exclusively to tmp_path and never touch repo."""
    test_override_file = tmp_path / "config_override.json"
    test_override_file.write_text(
        '{"deployment_mode": "PAPER", "trading_enabled": false, "shadow_mode": true, "order_size_inr": 200.0}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_OVERRIDE_PATH", str(test_override_file))
    try:
        from core.config import invalidate_config
        invalidate_config()
    except Exception:
        pass


# ── Shared test helpers ───────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone  # noqa: E402


def generate_synthetic_candles(count: int = 50, start_price: float = 100.0) -> list[dict]:
    """Generate a synthetic candle series for backtest / feedback testing."""
    candles = []
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    price = start_price
    for i in range(count):
        t_str = (base_time + timedelta(minutes=5 * i)).isoformat()
        change = 1.5 if i % 2 == 0 else -0.8
        open_p = price
        close_p = price + change
        high_p = max(open_p, close_p) + 0.5
        low_p = min(open_p, close_p) - 0.5
        price = close_p
        candles.append({
            "timestamp": t_str,
            "open": round(open_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "close": round(close_p, 2),
            "volume": 100.0 + i * 2,
        })
    return candles
