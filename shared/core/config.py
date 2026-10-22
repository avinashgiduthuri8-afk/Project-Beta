"""Canonical, paper-only configuration for Project Beta v1."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False,
        extra="ignore", populate_by_name=True,
    )

    execution_mode: Literal["PAPER"] = Field(
        default="PAPER", validation_alias=AliasChoices("EXECUTION_MODE", "execution_mode")
    )
    deployment_mode: Literal["PAPER"] = Field(
        default="PAPER", validation_alias=AliasChoices("DEPLOYMENT_MODE", "deployment_mode")
    )
    trading_enabled: bool = Field(
        default=False, validation_alias=AliasChoices("TRADING_ENABLED", "trading_enabled")
    )
    broker: Literal["PAPER"] = Field(default="PAPER", validation_alias=AliasChoices("BROKER", "broker"))
    market_data_provider: Literal["YAHOO", "DHAN", "TRADINGVIEW"] = Field(
        default="YAHOO",
        validation_alias=AliasChoices("MARKET_DATA_PROVIDER", "PUBLIC_MARKET_DATA_PROVIDER", "market_data_provider"),
    )
    dhan_client_id: str | None = Field(default=None, validation_alias=AliasChoices("DHAN_CLIENT_ID", "dhan_client_id"))
    dhan_access_token: str | None = Field(default=None, validation_alias=AliasChoices("DHAN_ACCESS_TOKEN", "dhan_access_token"))
    tradingview_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TRADINGVIEW_API_KEY", "tradingview_api_key"),
    )

    db_path: str = Field(default="data/project_beta.db", validation_alias=AliasChoices("DB_PATH", "db_path"))
    scanner_db_path: str = Field(default="data/project_beta.db", validation_alias=AliasChoices("SCANNER_DB_PATH", "scanner_db_path"))
    paper_db_path: str = Field(default="data/project_beta.db", validation_alias=AliasChoices("PAPER_DB_PATH", "paper_db_path"))
    learner_db_path: str = Field(default="data/project_beta.db", validation_alias=AliasChoices("LEARNER_DB_PATH", "learner_db_path"))
    scanner_poll_interval: int = Field(default=60, ge=5)
    paper_poll_interval: int = Field(default=5, ge=1)
    learner_poll_interval: int = Field(default=3600, ge=60)
    initial_paper_cash: float = Field(default=15_000.0, gt=0)
    order_size_inr: float = Field(default=1_000.0, gt=0, le=1_000.0)
    price_ceiling_inr: float = Field(default=250.0, gt=0, le=250.0)
    price_floor_inr: float = Field(default=0.0, ge=0, le=0)
    daily_loss_warning_inr: float = Field(default=750.0, gt=0, le=750.0)
    daily_entry_block_inr: float = Field(default=900.0, gt=0, le=900.0)
    daily_loss_limit_inr: float = Field(default=1_000.0, gt=0, le=1_000.0)
    max_open_positions: int = Field(default=3, ge=1, le=3)
    max_positions_per_sector: int = Field(default=1, ge=1, le=1)
    max_consecutive_losses: int = Field(default=3, ge=1, le=3)
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_CHAT_ID", "telegram_chat_id"),
    )
    learner_min_trades: int = Field(default=3, ge=1)

    @field_validator("execution_mode", "deployment_mode", mode="before")
    @classmethod
    def paper_only(cls, value: Any) -> str:
        if str(value or "").strip().upper() != "PAPER":
            raise ValueError("Beta v1 is paper-only")
        return "PAPER"

    @field_validator("trading_enabled", mode="before")
    @classmethod
    def trading_must_remain_disabled(cls, value: Any) -> bool:
        enabled = value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}
        if enabled:
            raise ValueError("Beta v1 is paper-only")
        return False

    @field_validator("market_data_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: Any) -> str:
        return str(value or "").strip().upper()

    @model_validator(mode="after")
    def shared_database_and_risk_order(self) -> "AppConfig":
        paths = {self.db_path, self.scanner_db_path, self.paper_db_path, self.learner_db_path}
        if len(paths) != 1:
            raise ValueError("Scanner, Paper, and Learner must use the shared Beta database")
        if not (
            self.daily_loss_warning_inr
            < self.daily_entry_block_inr
            < self.daily_loss_limit_inr
        ):
            raise ValueError("Daily loss thresholds must satisfy warning < block < limit")
        return self

    def validate_v1_safety(self) -> None:
        if self.execution_mode != "PAPER" or self.deployment_mode != "PAPER" or self.trading_enabled or self.broker != "PAPER":
            raise ValueError("Beta v1 is paper-only")
        if self.market_data_provider == "DHAN" and not (self.dhan_client_id and self.dhan_access_token):
            raise ValueError("Dhan market data requires DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN")

    @property
    def public_market_data_provider(self) -> str:
        return self.market_data_provider

    @property
    def v2_deployment_mode(self) -> str:
        return self.deployment_mode

    @property
    def v2_trading_enabled(self) -> bool:
        return self.trading_enabled

    @property
    def v2_db_path(self) -> str:
        return self.db_path

    def get_sanitized_config_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        for key in ("dhan_client_id", "dhan_access_token", "telegram_bot_token"):
            if data.get(key):
                data[key] = "***REDACTED***"
        return data


V2Config = AppConfig


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    config = AppConfig()
    config.validate_v1_safety()
    return config


def invalidate_config() -> None:
    get_config.cache_clear()
