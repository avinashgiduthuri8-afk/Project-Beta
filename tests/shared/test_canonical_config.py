"""
Tests for canonical configuration (AppConfig) and backward-compatibility aliases.
"""

from core.config import AppConfig, V2Config


def test_v2config_is_appconfig():
    """Verify V2Config is an alias for AppConfig."""
    assert V2Config is AppConfig


def test_canonical_defaults():
    """Verify canonical defaults on fresh AppConfig without env file."""
    cfg = AppConfig(_env_file=None)
    assert cfg.trading_enabled is False
    assert cfg.deployment_mode == "PAPER"
    assert cfg.shadow_mode is False
    assert cfg.execution_timeout == 30.0
    assert cfg.db_path == "data/project_alpha.db"


def test_backward_compatibility_property_and_attr_access():
    """Verify v2_* attributes resolve seamlessly to canonical fields."""
    cfg = AppConfig()
    # Properties
    assert cfg.v2_trading_enabled == cfg.trading_enabled
    assert cfg.v2_deployment_mode == cfg.deployment_mode
    assert cfg.v2_shadow_mode == cfg.shadow_mode
    assert cfg.v2_execution_timeout == cfg.execution_timeout
    assert cfg.v2_db_path == cfg.db_path

    # Dynamic __getattr__
    assert cfg.v2_scanner_poll_interval == cfg.scanner_poll_interval
    assert cfg.v2_ai_enabled == cfg.ai_enabled
    assert cfg.v2_metrics_snapshot_interval == cfg.metrics_snapshot_interval


def test_backward_compatibility_setattr():
    """Verify setting v2_* updates canonical field."""
    cfg = AppConfig()
    cfg.v2_trading_enabled = True
    assert cfg.trading_enabled is True
    assert cfg.v2_trading_enabled is True

    cfg.v2_scanner_poll_interval = 45
    assert cfg.scanner_poll_interval == 45
    assert cfg.v2_scanner_poll_interval == 45


def test_model_copy_with_legacy_keys():
    """Verify model_copy updates properly when given v2_* keys."""
    cfg = AppConfig()
    copy = cfg.model_copy(
        update={"v2_trading_enabled": True, "v2_scanner_poll_interval": 33}
    )
    assert copy.trading_enabled is True
    assert copy.scanner_poll_interval == 33


def test_sanitized_config_dict():
    """Verify sanitized config dict contains both canonical and v2_* keys."""
    cfg = AppConfig()
    data = cfg.get_sanitized_config_dict()
    assert "trading_enabled" in data
    assert "v2_trading_enabled" in data
    assert data["trading_enabled"] == data["v2_trading_enabled"]
