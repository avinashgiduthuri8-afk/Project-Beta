"""
Project Beta paper-trading configuration.

Paper configuration MUST explicitly contain:
  MODE = "PAPER"
  DB_PATH = "paper/data/beta_paper.db"
"""

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaperConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: str = "PAPER"
    db_path: str = Field(
        default="paper/data/beta_paper.db",
        validation_alias=AliasChoices("PAPER_DB_PATH", "DB_PATH", "paper_db_path", "db_path"),
    )
    scanner_db_path: str = Field(
        default="scanner/data/beta_scanner.db",
        validation_alias=AliasChoices("SCANNER_DB_PATH", "scanner_db_path"),
    )
    trading_enabled: bool = True
    order_size_inr: float = 10_000.0
    max_concurrent_positions: int = 20


def get_paper_config() -> PaperConfig:
    cfg = PaperConfig()
    if cfg.mode.upper() != "PAPER":
        raise ValueError(f"PaperConfig corrupted: mode must be 'PAPER', got '{cfg.mode}'")
    return cfg
