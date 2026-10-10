import pytest
from unittest.mock import AsyncMock, MagicMock
from core.config import AppConfig
from background.production.safety_gate import verify_live_mode_safety_gate
from telegram.formatters import format_telegram_capital, format_telegram_status, format_telegram_health


@pytest.mark.asyncio
async def test_safety_gate_missing_credentials():
    config = AppConfig()
    config.coindcx_api_key = None
    config.coindcx_api_secret = None
    config.coindcx_live_api_key = None
    config.coindcx_live_api_secret = None
    passed, reason, bal = await verify_live_mode_safety_gate(config, None, None)
    assert not passed
    assert "Missing CoinDCX API credentials" in reason


@pytest.mark.asyncio
async def test_safety_gate_insufficient_balance():
    config = AppConfig()
    config.coindcx_api_key = "valid_key"
    config.coindcx_api_secret = "valid_secret"
    config.order_size_inr = 250.0

    sub_mgr = MagicMock()
    sub_mgr.get_live_balance = AsyncMock(return_value={"success": True, "inr_balance": 0.00235})

    passed, reason, bal = await verify_live_mode_safety_gate(config, sub_mgr, None)
    assert not passed
    assert "Insufficient CoinDCX INR balance" in reason
    assert bal == 0.00235


@pytest.mark.asyncio
async def test_safety_gate_coindcx_unavailable():
    config = AppConfig()
    config.coindcx_api_key = "valid_key"
    config.coindcx_api_secret = "valid_secret"
    config.order_size_inr = 200.0

    sub_mgr = MagicMock()
    sub_mgr.get_live_balance = AsyncMock(return_value={"success": False, "error": "Unauthorized / auth failed"})

    passed, reason, bal = await verify_live_mode_safety_gate(config, sub_mgr, None)
    assert not passed
    assert "CoinDCX API balance retrieval failed" in reason
    assert bal is None


@pytest.mark.asyncio
async def test_safety_gate_circuit_breaker_tripped():
    config = AppConfig()
    config.coindcx_api_key = "valid_key"
    config.coindcx_api_secret = "valid_secret"
    config.order_size_inr = 200.0

    sub_mgr = MagicMock()
    sub_mgr.get_live_balance = AsyncMock(return_value={"success": True, "inr_balance": 1000.0})

    risk_service = MagicMock()
    cb = MagicMock()
    cb.is_open = True
    cb.emergency_stop = False
    risk_service.circuit_breaker = cb

    passed, reason, bal = await verify_live_mode_safety_gate(config, sub_mgr, risk_service)
    assert not passed
    assert "Circuit breaker is open" in reason


@pytest.mark.asyncio
async def test_safety_gate_all_checks_passed():
    config = AppConfig()
    config.coindcx_api_key = "valid_key"
    config.coindcx_api_secret = "valid_secret"
    config.order_size_inr = 200.0

    sub_mgr = MagicMock()
    sub_mgr.get_live_balance = AsyncMock(return_value={"success": True, "inr_balance": 5000.0})

    risk_service = MagicMock()
    cb = MagicMock()
    cb.is_open = False
    cb.emergency_stop = False
    risk_service.circuit_breaker = cb

    passed, reason, bal = await verify_live_mode_safety_gate(config, sub_mgr, risk_service)
    assert passed
    assert reason == "READY"
    assert bal == 5000.0


def test_formatters_coindcx_unavailable():
    """Verify formatters correctly display COINDCX_UNAVAILABLE when balance is missing in LIVE mode."""
    # /capital formatter
    cap_data = {
        "mode": "LIVE_MICROCASH",
        "available_capital": None,
        "deployed_capital": 0.0,
        "source": "COINDCX_UNAVAILABLE",
    }
    text = format_telegram_capital(cap_data)
    assert "COINDCX_UNAVAILABLE" in text

    # /status formatter
    status_data = {
        "mode": "LIVE_MICROCASH",
        "available_capital": None,
        "order_amount_inr": 200.0,
    }
    status_text = format_telegram_status(status_data)
    assert "COINDCX_UNAVAILABLE" in status_text

    # /health formatter
    health_data = {
        "mode": "LIVE_MICROCASH",
        "components": {
            "scanner": True,
            "ai": True,
            "risk": True,
            "execution": True,
            "database": True,
            "event_bus": True,
            "coindcx": False,
        },
        "overall": "DEGRADED",
    }
    health_text = format_telegram_health(health_data)
    assert "🔴" in health_text
