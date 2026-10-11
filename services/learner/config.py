"""
Project Beta autonomous learner configuration (stages 8–10).

Learner configuration explicitly declares:
  MODE = "LEARNER"
  DB_PATH = "learner/data/beta_learner.db"
  PAPER_DB_PATH = "paper/data/beta_paper.db"
  SCANNER_DB_PATH = "scanner/data/beta_scanner.db"
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LearnerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: str = "LEARNER"
    db_path: str = Field(
        default="learner/data/beta_learner.db",
        validation_alias=AliasChoices(
            "LEARNER_DB_PATH", "learner_db_path", "LEARNING_DB_PATH"
        ),
    )
    paper_db_path: str = Field(
        default="paper/data/beta_paper.db",
        validation_alias=AliasChoices("PAPER_DB_PATH", "paper_db_path"),
    )
    scanner_db_path: str = Field(
        default="scanner/data/beta_scanner.db",
        validation_alias=AliasChoices("SCANNER_DB_PATH", "scanner_db_path"),
    )
    poll_interval_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "LEARNER_POLL_INTERVAL", "learner_poll_interval_seconds"
        ),
    )
    min_trades_for_analysis: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "LEARNER_MIN_TRADES", "learner_min_trades"
        ),
    )
    enable_prevalidation_backtest: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "LEARNER_ENABLE_BACKTEST", "enable_prevalidation_backtest"
        ),
    )
    learner_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LEARNER_BOT_TOKEN", "learner_bot_token", "TELEGRAM_BOT_TOKEN"
        ),
    )
    learner_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LEARNER_CHAT_ID", "learner_chat_id", "TELEGRAM_CHAT_ID"
        ),
    )


def get_learner_config() -> LearnerConfig:
    cfg = LearnerConfig()
    if cfg.mode.upper() != "LEARNER":
        raise ValueError(
            f"LearnerConfig corrupted: mode must be 'LEARNER', got '{cfg.mode}'"
        )
    return cfg
