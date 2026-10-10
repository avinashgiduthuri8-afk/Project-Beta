"""
Unit and Integration Tests for CoinDCX Master API Key Authentication & Unified Execution Engine.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.config import AppConfig
from core.types import BotName
from execution.trading.subaccount_manager import (
    CoinDCXExecutionClient,
    CoinDCXExecutionManager,
    SubAccountConfig,
)

# ── 1. Master API Credentials & Config Loading ───────────────────────────────


def test_master_api_credentials_loaded_from_env(monkeypatch):
    monkeypatch.setenv("COINDCX_API_KEY", "test_master_key_123")
    monkeypatch.setenv("COINDCX_API_SECRET", "test_master_secret_456")

    cfg = AppConfig()
    assert cfg.coindcx_api_key == "test_master_key_123"
    assert cfg.coindcx_api_secret == "test_master_secret_456"

    mgr = CoinDCXExecutionManager()
    client = mgr.get_client(BotName.STE)
    assert client.config.api_key == "test_master_key_123"
    assert client.config.api_secret == "test_master_secret_456"


def test_hmac_sha256_auth_headers_generation():
    config = SubAccountConfig(
        bot_name=BotName.STE,
        subaccount_id="ALPHA_STE_01",
        api_key="test_key_abc",
        api_secret="test_secret_xyz",
    )
    client = CoinDCXExecutionClient(config=config)

    payload = {
        "side": "buy",
        "market": "SOLINR",
        "price_per_unit": 12500.0,
        "total_quantity": 0.02,
    }
    headers = client.generate_auth_headers(payload)

    assert headers["Content-Type"] == "application/json"
    assert headers["X-AUTH-APIKEY"] == "test_key_abc"
    assert "X-AUTH-SIGNATURE" in headers
    assert len(headers["X-AUTH-SIGNATURE"]) == 64  # SHA256 hex string length
    assert "timestamp" in payload


# ── 2. Diagnostic Balance Fetching (POST /exchange/v1/users/balances) ─────────


@pytest.mark.anyio
async def test_get_balances_success_mock():
    mock_balances = [
        {"currency": "INR", "balance": "10000.00", "locked_balance": "500.00"},
        {"currency": "BTC", "balance": "0.05", "locked_balance": "0.00"},
        {"currency": "USDT", "balance": "250.00", "locked_balance": "0.00"},
    ]

    async def mock_handler(request: httpx.Request):
        assert request.url.path == "/exchange/v1/users/balances"
        assert request.headers.get("X-AUTH-APIKEY") == "test_key_abc"
        assert "X-AUTH-SIGNATURE" in request.headers
        body = json.loads(request.content.decode("utf-8"))
        assert "timestamp" in body
        return httpx.Response(200, json=mock_balances)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        config = SubAccountConfig(
            bot_name=BotName.STE,
            subaccount_id="ALPHA_STE_01",
            api_key="test_key_abc",
            api_secret="test_secret_xyz",
        )
        client = CoinDCXExecutionClient(config=config)
        res = await client.get_balances(client=http_client)

        assert res["success"] is True
        assert res["status_code"] == 200
        assert res["inr_balance"] == 10000.00
        assert res["inr_locked"] == 500.00
        assert len(res["balances"]) == 3


@pytest.mark.anyio
async def test_get_balances_auth_failure_401():
    async def mock_handler(request: httpx.Request):
        return httpx.Response(401, json={"message": "Invalid API key or signature"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        config = SubAccountConfig(
            bot_name=BotName.STE,
            subaccount_id="ALPHA_STE_01",
            api_key="invalid_key",
            api_secret="invalid_secret",
        )
        client = CoinDCXExecutionClient(config=config)
        res = await client.get_balances(client=http_client)

        assert res["success"] is False
        assert res["status_code"] == 401
        assert res["error"] == "AUTH_FAILED"


@pytest.mark.anyio
async def test_get_balances_rate_limited_429():
    async def mock_handler(request: httpx.Request):
        return httpx.Response(429, json={"message": "Rate limit exceeded"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        config = SubAccountConfig(
            bot_name=BotName.STE,
            subaccount_id="ALPHA_STE_01",
            api_key="test_key_abc",
            api_secret="test_secret_xyz",
        )
        client = CoinDCXExecutionClient(config=config)
        res = await client.get_balances(client=http_client)

        assert res["success"] is False
        assert res["status_code"] == 429
        assert res["error"] == "RATE_LIMIT_EXCEEDED"


# ── 3. Live Order Dispatch & Precision Enforcement ────────────────────────────


@pytest.mark.anyio
async def test_place_live_order_success_mock(monkeypatch):
    from core.config import get_config

    live_cfg = get_config().model_copy(
        update={"v2_deployment_mode": "LIVE_MICROCASH", "v2_trading_enabled": True}
    )
    monkeypatch.setattr("core.config.get_config", lambda: live_cfg)
    mock_order_response = {
        "id": "ORD_COINDCX_9999",
        "market": "SOLINR",
        "side": "buy",
        "price_per_unit": 12500.0,
        "total_quantity": 0.02,
        "status": "open",
    }

    async def mock_handler(request: httpx.Request):
        if request.url.path == "/exchange/v1/users/balances":
            return httpx.Response(
                200,
                json=[{"currency": "INR", "balance": 10000.0, "locked_balance": 0.0}],
            )
        if request.url.path == "/exchange/v1/orders/create":
            body = json.loads(request.content.decode("utf-8"))
            assert body["market"] == "SOLINR"
            assert body["side"] == "buy"
            assert body["price_per_unit"] == 12500.0
            assert body["total_quantity"] == 0.02
            return httpx.Response(200, json=mock_order_response)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        config = SubAccountConfig(
            bot_name=BotName.STE,
            subaccount_id="ALPHA_STE_01",
            api_key="test_key_abc",
            api_secret="test_secret_xyz",
            allocated_wallet_inr=10000.0,
        )
        client = CoinDCXExecutionClient(config=config)
        res = await client.place_live_order(
            pair="SOL/INR",
            side="BUY",
            price=12500.0,
            qty=0.02,
            client=http_client,
        )

        assert res["success"] is True
        assert res["status_code"] == 200
        assert res["notional_inr"] == 250.0
        assert res["order"]["id"] == "ORD_COINDCX_9999"


@pytest.mark.anyio
async def test_place_live_order_min_notional_rejection(monkeypatch):
    from core.config import get_config

    live_cfg = get_config().model_copy(
        update={"v2_deployment_mode": "LIVE_MICROCASH", "v2_trading_enabled": True}
    )
    monkeypatch.setattr("core.config.get_config", lambda: live_cfg)
    config = SubAccountConfig(
        bot_name=BotName.BBS,
        subaccount_id="ALPHA_BBS_01",
        api_key="test_key_abc",
        api_secret="test_secret_xyz",
    )
    client = CoinDCXExecutionClient(config=config)
    # 1 DOGE @ ₹16.50 = ₹16.50 (< ₹100 min notional)
    res = await client.place_live_order(
        pair="DOGE/INR",
        side="BUY",
        price=16.50,
        qty=1.0,
    )
    assert res["success"] is False
    assert res["error"] == "ORDER_NOTIONAL_BELOW_MINIMUM"


# ── 4. Order Status & Cancellation ───────────────────────────────────────────


@pytest.mark.anyio
async def test_get_order_status_and_cancel_mock():
    async def mock_handler(request: httpx.Request):
        if request.url.path == "/exchange/v1/orders/status":
            return httpx.Response(200, json={"id": "ORD_123", "status": "filled"})
        if request.url.path == "/exchange/v1/orders/cancel":
            return httpx.Response(200, json={"id": "ORD_123", "status": "cancelled"})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        config = SubAccountConfig(
            bot_name=BotName.HDA,
            subaccount_id="ALPHA_HDA_01",
            api_key="test_key_abc",
            api_secret="test_secret_xyz",
        )
        client = CoinDCXExecutionClient(config=config)

        status_res = await client.get_order_status("ORD_123", client=http_client)
        assert status_res["success"] is True
        assert status_res["order"]["status"] == "filled"

        cancel_res = await client.cancel_order("ORD_123", client=http_client)
        assert cancel_res["success"] is True
        assert cancel_res["result"]["status"] == "cancelled"


# ── 5. Manager Connectivity Diagnostic ────────────────────────────────────────


@pytest.mark.anyio
async def test_manager_check_account_connectivity():
    mock_balances = [
        {"currency": "INR", "balance": "10000.00", "locked_balance": "0.00"},
    ]

    async def mock_handler(request: httpx.Request):
        return httpx.Response(200, json=mock_balances)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = CoinDCXExecutionManager()
        res = await mgr.check_account_connectivity(client=http_client)
        assert res["success"] is True
        assert res["inr_balance"] == 10000.00
