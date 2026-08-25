"""Configuration loader and validator for Project-Beta using Pydantic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class SymbolConfig(BaseModel):
    exchange: str = "NSE"
    tradingsymbol: str
    token: str
    lot_size: int = 1
    tick_size: float = 0.05


class MarketHoursConfig(BaseModel):
    pre_open_time: str = "09:00:00"
    market_open_time: str = "09:15:00"
    auto_square_off_time: str = "15:15:00"
    market_close_time: str = "15:30:00"


class RiskConfig(BaseModel):
    initial_capital: float = 100000.0
    max_daily_loss: float = 3000.0
    max_risk_per_trade_pct: float = 1.0
    max_open_positions: int = 3
    max_orders_per_second: int = 5
    slippage_pct: float = 0.05


class StrategyConfig(BaseModel):
    name: str = "VWAP_Momentum"
    timeframe_minutes: int = 5
    lookback_periods: int = 20
    target_rr_ratio: float = 2.0
    stop_loss_pct: float = 0.8


class StorageConfig(BaseModel):
    database_path: str = "storage/trades.db"
    journal_csv_path: str = "storage/trade_journal.csv"


class NotificationConfig(BaseModel):
    telegram_enabled: bool = False
    discord_enabled: bool = False


class TradingConfig(BaseModel):
    mode: str = "paper"
    broker: str = "paper"
    timezone: str = "Asia/Kolkata"
    symbols: List[SymbolConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    trading: TradingConfig = Field(default_factory=TradingConfig)
    market_hours: MarketHoursConfig = Field(default_factory=MarketHoursConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from yaml file."""
    if config_path is None:
        base_dir = Path(__file__).resolve().parent
        config_path = str(base_dir / "settings.yaml")

    if not os.path.exists(config_path):
        return AppConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        raw_dict = yaml.safe_load(f) or {}

    return AppConfig.model_validate(raw_dict)
