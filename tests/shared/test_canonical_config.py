"""Canonical configuration tests for the paper-only Beta v1 runtime."""

import pytest
from pydantic import ValidationError

from core.config import AppConfig, V2Config


def test_v2config_remains_an_appconfig_alias():
    assert V2Config is AppConfig


def test_beta_v1_defaults_are_paper_only_and_keyless():
    cfg = AppConfig(_env_file=None)

    assert cfg.execution_mode == "PAPER"
    assert cfg.market_data_provider == "YAHOO"
    assert cfg.dhan_client_id is None
    assert cfg.dhan_access_token is None
    assert cfg.db_path == "data/project_beta.db"
    cfg.validate_v1_safety()


@pytest.mark.parametrize("mode", ["LIVE", "LIVE_MICROCASH", "UNKNOWN"])
def test_beta_v1_rejects_non_paper_execution_modes(mode):
    with pytest.raises(ValidationError, match="Beta v1 is paper-only"):
        AppConfig(_env_file=None, execution_mode=mode)


def test_beta_v1_accepts_dhan_only_as_market_data():
    cfg = AppConfig(
        _env_file=None,
        market_data_provider="DHAN",
        dhan_client_id="client",
        dhan_access_token="token",
    )
    cfg.validate_v1_safety()


def test_beta_v1_rejects_unknown_market_data_provider():
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, market_data_provider="ORDER_BROKER")

