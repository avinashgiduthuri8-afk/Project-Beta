"""Safety checks that keep live order placement outside Beta v1."""

import importlib.util

import pytest
from pydantic import ValidationError

from core.config import AppConfig


def test_v1_has_no_live_service_or_broker_order_modules():
    assert importlib.util.find_spec("services.live_bot") is None
    assert importlib.util.find_spec("execution.brokers") is None


def test_config_rejects_live_mode():
    with pytest.raises(ValidationError, match="Beta v1 is paper-only"):
        AppConfig(_env_file=None, execution_mode="LIVE")
