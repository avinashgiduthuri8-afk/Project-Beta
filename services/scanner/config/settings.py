"""
PROJECT-ALPHA — Scanner Service Configuration.
"""

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScannerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: str = "SCANNER"
    db_path: str = Field(
        default="scanner/data/beta_scanner.db",
        validation_alias=AliasChoices("SCANNER_DB_PATH", "DB_PATH", "scanner_db_path", "db_path"),
    )
    learner_db_path: str = Field(
        default="learner/data/beta_learner.db",
        validation_alias=AliasChoices("LEARNER_DB_PATH", "learner_db_path"),
    )
    poll_interval: int = 60
    signal_ttl: int = 300
    breakout_ttl: int = 300
    min_24h_turnover: float = 100000000.0
    max_spread_pct: float = 1.0
    breakout_rsi_max: float = 72.0
    min_priority: str = "Medium"
    strict_confluence_threshold: int = 85
    max_signals_per_cycle: int = 2


def get_scanner_config() -> ScannerConfig:
    return ScannerConfig()
