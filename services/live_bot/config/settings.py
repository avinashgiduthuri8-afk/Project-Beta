"""
PROJECT-ALPHA — Live Trading Configuration.

Live configuration MUST explicitly contain:
  MODE = "LIVE"
  DB_PATH = "live/data/alpha_live.db"
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: str = "LIVE"
    db_path: str = "live/data/beta_live.db"
    trading_enabled: bool = False  # Default FALSE until explicitly enabled by operator
    order_size_inr: float = 10_000.0
    max_concurrent_positions: int = 20


def get_live_config() -> LiveConfig:
    cfg = LiveConfig()
    if cfg.mode.upper() not in ("LIVE", "LIVE_MICROCASH"):
        raise ValueError(f"LiveConfig corrupted: mode must be 'LIVE', got '{cfg.mode}'")
    return cfg
