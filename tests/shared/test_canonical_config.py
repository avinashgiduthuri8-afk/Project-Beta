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
    assert cfg.scanner_db_path == cfg.db_path
    assert cfg.paper_db_path == cfg.db_path
    assert cfg.learner_db_path == cfg.db_path
    assert cfg.price_floor_inr == 0
    assert cfg.price_ceiling_inr == 250
    assert cfg.order_size_inr == 1000
    assert cfg.daily_loss_warning_inr == 750
    assert cfg.daily_entry_block_inr == 900
    assert cfg.daily_loss_limit_inr == 1000
    assert cfg.max_open_positions == 3
    assert cfg.max_positions_per_sector == 1
    assert cfg.max_consecutive_losses == 3
    assert cfg.telegram_bot_token is None
    assert cfg.telegram_chat_id is None
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"scanner_db_path": "scanner.db"},
        {"paper_db_path": "paper.db"},
        {"learner_db_path": "learner.db"},
        {"order_size_inr": 1000.01},
        {"daily_loss_limit_inr": 1000.01},
        {"daily_loss_warning_inr": 901},
        {"daily_entry_block_inr": 1001},
    ],
)
def test_beta_v1_rejects_unsafe_database_or_risk_overrides(overrides):
    with pytest.raises((ValidationError, ValueError)):
        cfg = AppConfig(_env_file=None, **overrides)
        cfg.validate_v1_safety()
