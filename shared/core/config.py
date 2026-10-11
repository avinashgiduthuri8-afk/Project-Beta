"""
PROJECT-ALPHA — Core Configuration System.

Canonical Pydantic BaseSettings model for application configuration.
Rules:
  - No service ever calls os.getenv() directly.
  - Environment variables are read once at startup and stored here.
  - A subset of keys is hot-reloadable via config_override.json.
  - Capital limits require a restart to change.

Canonical configuration class: AppConfig (aliased as V2Config for backward compatibility)
Canonical environment variables:
  - TRADING_ENABLED (fallback: V2_TRADING_ENABLED)
  - DEPLOYMENT_MODE (fallback: V2_DEPLOYMENT_MODE)
  - SHADOW_MODE (fallback: V2_SHADOW_MODE)
  - EXECUTION_TIMEOUT (fallback: V2_EXECUTION_TIMEOUT)
  - DB_PATH (fallback: V2_DB_PATH)

Canonical database path: data/project_alpha.db

Usage:
    from core.config import AppConfig, get_config
    cfg = get_config()
    print(cfg.trading_enabled)
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ORDER_AMOUNT_INR: float = 10_000.0

# Backward-compatibility mapping: production deployments may still use V2_* env vars
LEGACY_FIELD_MAP = {
    "v2_trading_enabled": "trading_enabled",
    "v2_deployment_mode": "deployment_mode",
    "v2_shadow_mode": "shadow_mode",
    "v2_execution_timeout": "execution_timeout",
    "v2_db_path": "db_path",
    "v2_enable_app_trading": "enable_app_trading",
}


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    db_path: str = Field(
        default="data/project_beta.db",
        validation_alias=AliasChoices("DB_PATH", "V2_DB_PATH", "db_path", "v2_db_path"),
        description="Path to the canonical SQLite database file.",
    )

    execution_mode: Literal["PAPER"] = Field(
        default="PAPER",
        validation_alias=AliasChoices("EXECUTION_MODE", "execution_mode"),
    )
    market_data_provider: Literal["YAHOO", "DHAN"] = Field(
        default="YAHOO",
        validation_alias=AliasChoices(
            "MARKET_DATA_PROVIDER",
            "PUBLIC_MARKET_DATA_PROVIDER",
            "market_data_provider",
            "public_market_data_provider",
        ),
    )

    # ── Unified Capital Pool & Sizing ─────────────────────────────────────────
    total_capital_limit: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TOTAL_CAPITAL_LIMIT", "CAPITAL_POOL", "total_capital_limit"
        ),
        description="Unified Capital Pool shared ceiling across all strategy bots (None = unconstrained/dynamic).",
    )
    trading_capital_pool: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CAPITAL_POOL", "TRADING_CAPITAL_POOL", "trading_capital_pool"
        ),
        description="Alias for unified capital pool.",
    )
    order_size_inr: float = Field(
        default=DEFAULT_ORDER_AMOUNT_INR,
        validation_alias=AliasChoices(
            "ORDER_SIZE_INR", "DEFAULT_TRADE_AMOUNT", "order_size_inr"
        ),
        description="Standard micro-order allocation (defaults to ₹200).",
    )
    max_concurrent_positions: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "MAX_CONCURRENT_POSITIONS", "max_concurrent_positions"
        ),
        description="Maximum concurrent fleet-wide open positions.",
    )
    enforce_single_coin_lock: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ENFORCE_SINGLE_COIN_LOCK", "enforce_single_coin_lock"
        ),
        description="Enforce single-position asset lock across all strategies.",
    )
    qpb_enabled: bool = Field(default=True, alias="QPB_ENABLED")
    qpb_max_portfolio_utilization_pct: float = Field(
        default=80.0, alias="QPB_MAX_PORTFOLIO_UTILIZATION_PCT"
    )
    qpb_max_position_allocation_pct: float = Field(
        default=20.0, alias="QPB_MAX_POSITION_ALLOCATION_PCT"
    )
    qpb_max_strategy_exposure_pct: float = Field(
        default=40.0, alias="QPB_MAX_STRATEGY_EXPOSURE_PCT"
    )
    qpb_max_coin_exposure_pct: float = Field(
        default=20.0, alias="QPB_MAX_COIN_EXPOSURE_PCT"
    )
    qpb_min_order_size_inr: float = Field(
        default=200.0, alias="QPB_MIN_ORDER_SIZE_INR"
    )
    qpb_min_risk_reward: float = Field(default=1.20, alias="QPB_MIN_RISK_REWARD")
    qpb_paper_db_path: str = Field(
        default="paper/data/beta_paper.db", alias="QPB_PAPER_DB_PATH"
    )

    # Master & Separated CoinDCX API Credentials (P0-05 Security Separation)
    coindcx_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COINDCX_API_KEY", "coindcx_api_key"),
    )
    coindcx_api_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COINDCX_API_SECRET", "coindcx_api_secret"),
    )
    coindcx_live_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COINDCX_LIVE_API_KEY", "coindcx_live_api_key"),
    )
    coindcx_live_api_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "COINDCX_LIVE_API_SECRET", "coindcx_live_api_secret"
        ),
    )
    coindcx_paper_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COINDCX_PAPER_API_KEY", "coindcx_paper_api_key"),
    )
    coindcx_paper_api_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "COINDCX_PAPER_API_SECRET", "coindcx_paper_api_secret"
        ),
    )

    # Indian broker credentials are always supplied explicitly to Beta.
    broker: str = Field(default="PAPER", alias="BROKER")
    exchange: str = Field(default="NSE", alias="EXCHANGE")
    product_type: str = Field(default="CNC", alias="PRODUCT_TYPE")
    zerodha_api_key: str | None = Field(default=None, alias="ZERODHA_API_KEY")
    zerodha_access_token: str | None = Field(default=None, alias="ZERODHA_ACCESS_TOKEN")
    dhan_client_id: str | None = Field(default=None, alias="DHAN_CLIENT_ID")
    dhan_access_token: str | None = Field(default=None, alias="DHAN_ACCESS_TOKEN")
    public_market_data_provider: str = Field(
        default="YAHOO", alias="PUBLIC_MARKET_DATA_PROVIDER"
    )

    # Strategy bot capital limits. 0 means no separate bot ceiling; the shared
    # pool (when configured) remains the only capital limit.
    ste_capital_limit: float = Field(default=0.0, alias="STE_CAPITAL_LIMIT")
    hda_capital_limit: float = Field(default=0.0, alias="HDA_CAPITAL_LIMIT")
    vcp_capital_limit: float = Field(default=0.0, alias="VCP_CAPITAL_LIMIT")
    bbs_capital_limit: float = Field(default=0.0, alias="BBS_CAPITAL_LIMIT")
    mtb_capital_limit: float = Field(default=0.0, alias="MTB_CAPITAL_LIMIT")
    nr7_capital_limit: float = Field(default=0.0, alias="NR7_CAPITAL_LIMIT")
    ppa_capital_limit: float = Field(default=0.0, alias="PPA_CAPITAL_LIMIT")
    mrb_capital_limit: float = Field(default=0.0, alias="MRB_CAPITAL_LIMIT")

    # ── Trade sizing & bot limits (Phase 5 / Unified Fleet) ───────────────────
    default_trade_amount_ste: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "STE_TRADE_AMOUNT",
            "V2_STE_TRADE_AMOUNT",
            "default_trade_amount_ste",
            "v2_default_trade_amount_ste",
        ),
    )
    default_trade_amount_hda: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "HDA_TRADE_AMOUNT",
            "V2_HDA_TRADE_AMOUNT",
            "default_trade_amount_hda",
            "v2_default_trade_amount_hda",
        ),
    )
    default_trade_amount_vcp: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "VCP_TRADE_AMOUNT",
            "V2_VCP_TRADE_AMOUNT",
            "default_trade_amount_vcp",
            "v2_default_trade_amount_vcp",
        ),
    )
    default_trade_amount_bbs: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "BBS_TRADE_AMOUNT",
            "V2_BBS_TRADE_AMOUNT",
            "default_trade_amount_bbs",
            "v2_default_trade_amount_bbs",
        ),
    )
    default_trade_amount_mtb: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "MTB_TRADE_AMOUNT",
            "V2_MTB_TRADE_AMOUNT",
            "default_trade_amount_mtb",
            "v2_default_trade_amount_mtb",
        ),
    )
    default_trade_amount_nr7: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "NR7_TRADE_AMOUNT",
            "V2_NR7_TRADE_AMOUNT",
            "default_trade_amount_nr7",
            "v2_default_trade_amount_nr7",
        ),
    )
    default_trade_amount_ppa: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "PPA_TRADE_AMOUNT",
            "V2_PPA_TRADE_AMOUNT",
            "default_trade_amount_ppa",
            "v2_default_trade_amount_ppa",
        ),
    )
    default_trade_amount_mrb: float = Field(
        default=200.0,
        validation_alias=AliasChoices(
            "MRB_TRADE_AMOUNT",
            "V2_MRB_TRADE_AMOUNT",
            "default_trade_amount_mrb",
            "v2_default_trade_amount_mrb",
        ),
    )
    max_positions_ste: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "STE_MAX_POSITIONS",
            "V2_STE_MAX_POSITIONS",
            "max_positions_ste",
            "v2_max_positions_ste",
        ),
    )
    max_positions_hda: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "HDA_MAX_POSITIONS",
            "V2_HDA_MAX_POSITIONS",
            "max_positions_hda",
            "v2_max_positions_hda",
        ),
    )
    max_positions_vcp: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "VCP_MAX_POSITIONS",
            "V2_VCP_MAX_POSITIONS",
            "max_positions_vcp",
            "v2_max_positions_vcp",
        ),
    )
    max_positions_bbs: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "BBS_MAX_POSITIONS",
            "V2_BBS_MAX_POSITIONS",
            "max_positions_bbs",
            "v2_max_positions_bbs",
        ),
    )
    max_positions_mtb: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "MTB_MAX_POSITIONS",
            "V2_MTB_MAX_POSITIONS",
            "max_positions_mtb",
            "v2_max_positions_mtb",
        ),
    )
    max_positions_nr7: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "NR7_MAX_POSITIONS",
            "V2_NR7_MAX_POSITIONS",
            "max_positions_nr7",
            "v2_max_positions_nr7",
        ),
    )
    max_positions_ppa: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "PPA_MAX_POSITIONS",
            "V2_PPA_MAX_POSITIONS",
            "max_positions_ppa",
            "v2_max_positions_ppa",
        ),
    )
    max_positions_mrb: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "MRB_MAX_POSITIONS",
            "V2_MRB_MAX_POSITIONS",
            "max_positions_mrb",
            "v2_max_positions_mrb",
        ),
    )
    max_consecutive_losses: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "MAX_CONSECUTIVE_LOSSES",
            "V2_MAX_CONSECUTIVE_LOSSES",
            "max_consecutive_losses",
            "v2_max_consecutive_losses",
        ),
        description="Max consecutive losses before circuit breaker trips.",
    )
    max_drawdown_pct: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "MAX_DRAWDOWN_PCT",
            "V2_MAX_DRAWDOWN_PCT",
            "max_drawdown_pct",
            "v2_max_drawdown_pct",
        ),
        description="Max daily drawdown pct before breaker trips.",
    )
    usdt_inr_rate: float = Field(
        default=91.50,
        validation_alias=AliasChoices(
            "USDT_INR_RATE",
            "V2_USDT_INR_RATE",
            "usdt_inr_rate",
            "v2_usdt_inr_rate",
        ),
        description="Configured exchange rate for USDT/USD to INR normalization.",
    )

    # ── Scanner ───────────────────────────────────────────────────────────────
    scanner_poll_interval: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "SCANNER_POLL_INTERVAL",
            "V2_SCANNER_POLL_INTERVAL",
            "scanner_poll_interval",
            "v2_scanner_poll_interval",
        ),
        description="Seconds between scanner polls.",
    )
    scanner_signal_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "SCANNER_SIGNAL_TTL",
            "V2_SCANNER_SIGNAL_TTL",
            "scanner_signal_ttl",
            "v2_scanner_signal_ttl",
        ),
        description="Seconds a signal remains live after generation.",
    )
    scanner_base_url: str = Field(
        default="http://localhost:5000/api/v1/scanner",
        validation_alias=AliasChoices(
            "SCANNER_BASE_URL",
            "V2_SCANNER_BASE_URL",
            "scanner_base_url",
            "v2_scanner_base_url",
        ),
        description="Base URL of the scanner HTTP API.",
    )
    scanner_min_priority: str = Field(
        default="Medium",
        validation_alias=AliasChoices(
            "SCANNER_MIN_PRIORITY",
            "V2_SCANNER_MIN_PRIORITY",
            "scanner_min_priority",
            "v2_scanner_min_priority",
        ),
        description="Minimum priority to persist (Elite|High|Medium|Watch|Ignore).",
    )
    scanner_max_signals: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "SCANNER_MAX_SIGNALS",
            "V2_SCANNER_MAX_SIGNALS",
            "scanner_max_signals",
            "v2_scanner_max_signals",
        ),
        description="Maximum high-conviction signals allowed per scanner cycle (default 2).",
    )
    scanner_strict_confluence_threshold: int = Field(
        default=85,
        validation_alias=AliasChoices(
            "SCANNER_STRICT_CONFLUENCE_THRESHOLD",
            "V2_SCANNER_STRICT_CONFLUENCE_THRESHOLD",
            "scanner_strict_confluence_threshold",
            "v2_scanner_strict_confluence_threshold",
        ),
        description="Minimum confluence score (0-100) required to accept a signal.",
    )
    scanner_dynamic_threshold_min: int = Field(
        default=80,
        validation_alias=AliasChoices(
            "SCANNER_DYNAMIC_THRESHOLD_MIN",
            "V2_SCANNER_DYNAMIC_THRESHOLD_MIN",
            "scanner_dynamic_threshold_min",
            "v2_scanner_dynamic_threshold_min",
        ),
        description="Minimum dynamic C2 threshold bound.",
    )
    scanner_dynamic_threshold_max: int = Field(
        default=92,
        validation_alias=AliasChoices(
            "SCANNER_DYNAMIC_THRESHOLD_MAX",
            "V2_SCANNER_DYNAMIC_THRESHOLD_MAX",
            "scanner_dynamic_threshold_max",
            "v2_scanner_dynamic_threshold_max",
        ),
        description="Maximum dynamic C2 threshold bound.",
    )
    # B1 Composite Ranking Weights
    scanner_ranking_weight_volume: float = Field(
        default=0.40,
        description="Weight for 24h volume in top-N composite ranking.",
    )
    scanner_ranking_weight_liquidity: float = Field(
        default=0.35,
        description="Weight for liquidity/spread in top-N composite ranking.",
    )
    scanner_ranking_weight_volatility: float = Field(
        default=0.25,
        description="Weight for ATR/volatility in top-N composite ranking.",
    )
    scanner_ranking_top_n: int = Field(
        default=50,
        description="Top N coins selected after composite ranking.",
    )
    scanner_max_spread_pct: float = Field(
        default=1.0,
        validation_alias=AliasChoices(
            "SCANNER_MAX_SPREAD_PCT",
            "V2_SCANNER_MAX_SPREAD_PCT",
            "scanner_max_spread_pct",
            "v2_scanner_max_spread_pct",
        ),
        description="Maximum bid/ask spread percentage allowed before candle fetches (default 1.0%).",
    )
    # B2 Filter Cascade Thresholds
    scanner_min_24h_volume: float = Field(
        default=200000.0,
        validation_alias=AliasChoices(
            "SCANNER_MIN_24H_VOLUME",
            "V2_SCANNER_MIN_24H_VOLUME",
            "scanner_min_24h_volume",
            "v2_scanner_min_24h_volume",
        ),
        description="Minimum 24h volume in INR for candidate coins (default ₹200,000).",
    )
    scanner_breakout_ttl: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "SCANNER_BREAKOUT_TTL",
            "V2_SCANNER_BREAKOUT_TTL",
            "scanner_breakout_ttl",
            "v2_scanner_breakout_ttl",
        ),
        description="Seconds a breakout signal remains live before expiry (default 300s).",
    )
    scanner_breakout_rsi_max: float = Field(
        default=72.0,
        validation_alias=AliasChoices(
            "SCANNER_BREAKOUT_RSI_MAX",
            "V2_SCANNER_BREAKOUT_RSI_MAX",
            "scanner_breakout_rsi_max",
            "v2_scanner_breakout_rsi_max",
        ),
        description="Maximum RSI allowed for breakout entries (default 72.0).",
    )
    scanner_max_price_change_pct: float = Field(
        default=25.0,
        description="Maximum absolute 24h price change % to reject pump/dump moves.",
    )
    scanner_min_atr_pct: float = Field(
        default=0.5,
        description="Minimum ATR % of price (volatility sanity floor).",
    )
    scanner_max_atr_pct: float = Field(
        default=12.0,
        description="Maximum ATR % of price (volatility sanity ceiling).",
    )
    scanner_market_sentiment_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "SCANNER_MARKET_SENTIMENT_ENABLED",
            "V2_SCANNER_MARKET_SENTIMENT_ENABLED",
            "scanner_market_sentiment_enabled",
            "v2_scanner_market_sentiment_enabled",
        ),
        description="Enable BTC/ETH Market Sentiment Layer.",
    )
    scanner_news_filter_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "SCANNER_NEWS_FILTER_ENABLED",
            "V2_SCANNER_NEWS_FILTER_ENABLED",
            "scanner_news_filter_enabled",
            "v2_scanner_news_filter_enabled",
        ),
        description="Enable News & Risk Event Filtering Layer.",
    )
    post_exit_cooldown_seconds: int = Field(
        default=900,
        validation_alias=AliasChoices(
            "POST_EXIT_COOLDOWN_SECONDS",
            "V2_POST_EXIT_COOLDOWN_SECONDS",
            "post_exit_cooldown_seconds",
            "v2_post_exit_cooldown_seconds",
        ),
        description="Post-exit cooldown window in seconds (default 900s / 15m) preventing immediate re-entry on the same coin.",
    )

    # ── WebSocket ─────────────────────────────────────────────────────────────
    ws_heartbeat_interval: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "WS_HEARTBEAT_INTERVAL",
            "V2_WS_HEARTBEAT_INTERVAL",
            "ws_heartbeat_interval",
            "v2_ws_heartbeat_interval",
        ),
    )
    ws_max_connections: int = Field(
        default=50,
        validation_alias=AliasChoices(
            "WS_MAX_CONNECTIONS",
            "V2_WS_MAX_CONNECTIONS",
            "ws_max_connections",
            "v2_ws_max_connections",
        ),
    )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    metrics_snapshot_interval: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "METRICS_SNAPSHOT_INTERVAL",
            "V2_METRICS_SNAPSHOT_INTERVAL",
            "metrics_snapshot_interval",
            "v2_metrics_snapshot_interval",
        ),
    )
    health_check_interval: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "HEALTH_CHECK_INTERVAL",
            "V2_HEALTH_CHECK_INTERVAL",
            "health_check_interval",
            "v2_health_check_interval",
        ),
    )
    event_log_retention_days: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "EVENT_LOG_RETENTION_DAYS",
            "V2_EVENT_LOG_RETENTION_DAYS",
            "event_log_retention_days",
            "v2_event_log_retention_days",
        ),
    )

    # ── Notification & Telegram Dual C2 (Paper vs Live) ─────────────────────
    alert_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ALERT_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "LIVE_BOT_TOKEN", "alert_bot_token"
        ),
    )
    alert_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ALERT_CHAT_ID", "TELEGRAM_CHAT_ID", "LIVE_CHAT_ID", "alert_chat_id"
        ),
    )
    paper_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PAPER_BOT_TOKEN", "TELEGRAM_PAPER_BOT_TOKEN", "paper_bot_token"
        ),
        description="Dedicated Telegram Bot Token for simulated Paper Trading and test alerts.",
    )
    paper_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PAPER_CHAT_ID", "TELEGRAM_PAPER_CHAT_ID", "paper_chat_id"
        ),
        description="Dedicated Chat ID for Paper Trading bot.",
    )
    scanner_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SCANNER_BOT_TOKEN", "TELEGRAM_SCANNER_BOT_TOKEN", "LIVE_BOT_TOKEN", "ALERT_BOT_TOKEN", "scanner_bot_token"
        ),
        description="Dedicated Telegram Bot Token for Scanner Bot signal alerts and C2.",
    )
    scanner_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SCANNER_CHAT_ID", "TELEGRAM_SCANNER_CHAT_ID", "LIVE_CHAT_ID", "ALERT_CHAT_ID", "scanner_chat_id"
        ),
        description="Dedicated Chat ID for Scanner Bot.",
    )
    live_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LIVE_BOT_TOKEN", "TELEGRAM_LIVE_BOT_TOKEN", "ALERT_BOT_TOKEN", "live_bot_token"
        ),
        description="Dedicated Telegram Bot Token for Real Money Live Trading.",
    )
    live_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LIVE_CHAT_ID", "TELEGRAM_LIVE_CHAT_ID", "ALERT_CHAT_ID", "live_chat_id"
        ),
        description="Dedicated Chat ID for Real Money Live Trading bot.",
    )
    telegram_interactive_enabled: bool = Field(
        default=True,
        description="Enable interactive Telegram polling interface (C2 bot).",
    )
    telegram_allowed_chat_ids: str | None = Field(
        default=None,
        description="Comma-separated whitelist of allowed Telegram chat/user IDs.",
    )

    # ── AI Intelligence (Phase 4) ─────────────────────────────────────────────
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    ai_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AI_ENABLED", "V2_AI_ENABLED", "ai_enabled", "v2_ai_enabled"
        ),
        description="Enable AI Intelligence Layer.",
    )
    ai_model: str = Field(
        default="gemini-flash-latest",
        validation_alias=AliasChoices(
            "AI_MODEL", "V2_AI_MODEL", "ai_model", "v2_ai_model"
        ),
        description="Gemini model identifier.",
    )
    ai_min_priority: str = Field(
        default="Medium",
        validation_alias=AliasChoices(
            "AI_MIN_PRIORITY",
            "V2_AI_MIN_PRIORITY",
            "ai_min_priority",
            "v2_ai_min_priority",
        ),
        description="Min signal priority to trigger AI evaluation.",
    )
    ai_confidence_threshold: int = Field(
        default=70,
        validation_alias=AliasChoices(
            "AI_CONFIDENCE_THRESHOLD",
            "V2_AI_CONFIDENCE_THRESHOLD",
            "ai_confidence_threshold",
            "v2_ai_confidence_threshold",
        ),
        description="Confidence threshold (0-100) to confirm trade signals.",
    )
    ai_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "AI_TIMEOUT_SECONDS",
            "V2_AI_TIMEOUT_SECONDS",
            "ai_timeout_seconds",
            "v2_ai_timeout_seconds",
        ),
        description="Timeout in seconds for AI API calls.",
    )
    ai_max_retries: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "AI_MAX_RETRIES", "V2_AI_MAX_RETRIES", "ai_max_retries", "v2_ai_max_retries"
        ),
        description="Max retries on AI call failures.",
    )
    ai_circuit_breaker_threshold: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "AI_CIRCUIT_BREAKER_THRESHOLD",
            "V2_AI_CIRCUIT_BREAKER_THRESHOLD",
            "ai_circuit_breaker_threshold",
            "v2_ai_circuit_breaker_threshold",
        ),
        description="Consecutive Gemini failures before circuit breaker trips to OPEN.",
    )
    ai_circuit_breaker_cooldown_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
            "V2_AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
            "ai_circuit_breaker_cooldown_seconds",
            "v2_ai_circuit_breaker_cooldown_seconds",
        ),
        description="Cooldown seconds before circuit breaker probes HALF_OPEN.",
    )

    operator_security_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPERATOR_SECURITY_PASSWORD",
            "operator_security_password",
        ),
        description="Operator security password required for live-mode transitions.",
    )

    # ── Canonical Fields ──────────────────────────────────────────────────────
    deployment_mode: str = Field(
        default="PAPER",
        validation_alias=AliasChoices(
            "DEPLOYMENT_MODE",
            "V2_DEPLOYMENT_MODE",
            "deployment_mode",
            "v2_deployment_mode",
        ),
        description="Execution mode: PAPER or LIVE (LIVE_MICROCASH).",
    )
    shadow_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SHADOW_MODE", "V2_SHADOW_MODE", "shadow_mode", "v2_shadow_mode"
        ),
        description="Run shadow execution alongside live/paper.",
    )
    trading_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "TRADING_ENABLED",
            "V2_TRADING_ENABLED",
            "trading_enabled",
            "v2_trading_enabled",
        ),
        description="Master switch enabling live and automated trade dispatch.",
    )
    enable_app_trading: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ENABLE_APP_TRADING",
            "V2_ENABLE_APP_TRADING",
            "enable_app_trading",
            "v2_enable_app_trading",
        ),
        description="Allow app.py to run embedded scanner and trading service. False for 3-service isolated architecture.",
    )
    execution_timeout: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "EXECUTION_TIMEOUT",
            "V2_EXECUTION_TIMEOUT",
            "execution_timeout",
            "v2_execution_timeout",
        ),
        description="Timeout in seconds for order execution and verification operations.",
    )

    # ── Backward Compatibility Properties for v2_* fields ────────────────────
    @property
    def v2_trading_enabled(self) -> bool:
        return self.trading_enabled

    @property
    def v2_enable_app_trading(self) -> bool:
        return self.enable_app_trading

    @property
    def v2_deployment_mode(self) -> str:
        return self.deployment_mode

    @property
    def v2_shadow_mode(self) -> bool:
        return self.shadow_mode

    @property
    def v2_execution_timeout(self) -> float:
        return self.execution_timeout

    @property
    def v2_db_path(self) -> str:
        return self.db_path

    @property
    def sqlite_db_path(self) -> str:
        return self.db_path

    def __getattr__(self, name: str) -> Any:
        if name.startswith("v2_"):
            canonical = name[3:]
            if canonical in self.__class__.model_fields:
                return getattr(self, canonical)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("v2_"):
            canonical = name[3:]
            if canonical in self.__class__.model_fields:
                super().__setattr__(canonical, value)
                return
        if name in LEGACY_FIELD_MAP:
            super().__setattr__(LEGACY_FIELD_MAP[name], value)
            return
        super().__setattr__(name, value)

    def model_copy(
        self, *, update: dict[str, Any] | None = None, deep: bool = False
    ) -> AppConfig:
        if update:
            normalized_update = {}
            for k, v in update.items():
                target_key = LEGACY_FIELD_MAP.get(k, k)
                if (
                    target_key.startswith("v2_")
                    and target_key[3:] in self.__class__.model_fields
                ):
                    target_key = target_key[3:]
                normalized_update[target_key] = v
            update = normalized_update
        return super().model_copy(update=update, deep=deep)

    @property
    def capital_pool(self) -> float | None:
        return (
            self.total_capital_limit
            if self.total_capital_limit is not None
            else self.trading_capital_pool
        )

    @property
    def telegram_bot_token(self) -> str | None:
        return self.alert_bot_token

    @property
    def telegram_chat_id(self) -> str | None:
        return self.alert_chat_id

    @field_validator("order_size_inr")
    @classmethod
    def validate_order_size(cls, v: float) -> float:
        return max(200.0, float(v))

    @field_validator("scanner_min_priority", "ai_min_priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        valid = {"Elite", "High", "Medium", "Watch", "Ignore"}
        if v not in valid:
            raise ValueError(f"Priority must be one of {valid}")
        return v

    def validate_live_security(self) -> None:
        """
        Validate security requirements for LIVE trading mode.
        Raises SecurityConfigError if LIVE mode is active without non-dummy API keys or operator password.
        """
        from core.exceptions import SecurityConfigError

        dep_mode = (self.deployment_mode or "").upper()
        if dep_mode in {"LIVE", "LIVE_MICROCASH"} and self.trading_enabled:
            DUMMY_VALUES = {
                "DUMMY_KEY",
                "SAMPLE_KEY",
                "ALPHA-PROD-KEY",
                "TEST",
                "SECRET",
                "12345",
                "CHANGE_ME",
                "DUMMY_SECRET",
                "DEMO",
                "SAMPLE",
                "",
            }

            broker = (self.broker or "PAPER").upper()
            if broker == "PAPER":
                raise SecurityConfigError(
                    "LIVE trading mode blocked: BROKER must be ZERODHA or DHAN."
                )
            if broker == "ZERODHA":
                values = (self.zerodha_api_key, self.zerodha_access_token)
            elif broker == "DHAN":
                values = (self.dhan_client_id, self.dhan_access_token)
            else:
                raise SecurityConfigError(
                    f"LIVE trading mode blocked: unsupported broker {broker}."
                )
            if any(
                not value or str(value).strip().upper() in DUMMY_VALUES
                for value in values
            ):
                raise SecurityConfigError(
                    f"LIVE trading mode blocked: valid Beta-only {broker} credentials are required."
                )

    def validate_v1_safety(self) -> None:
        """Fail closed if configuration attempts to enable order execution."""
        if self.execution_mode != "PAPER":
            raise ValueError("Beta v1 is paper-only")
        if self.market_data_provider not in {"YAHOO", "DHAN"}:
            raise ValueError("Beta v1 supports only YAHOO or DHAN market data")

    @field_validator("execution_mode", mode="before")
    @classmethod
    def validate_execution_mode_v1(cls, value: Any) -> str:
        mode = str(value or "").strip().upper()
        if mode != "PAPER":
            raise ValueError("Beta v1 is paper-only")
        return mode

    @field_validator("market_data_provider", mode="before")
    @classmethod
    def normalize_market_data_provider(cls, value: Any) -> str:
        return str(value or "").strip().upper()

    def get_sanitized_config_dict(self) -> dict:
        """Return dict of config values with secret keys safely redacted and legacy aliases supported."""
        data = self.model_dump()
        # Include supported legacy aliases for existing deployments.
        for legacy_key, canonical_key in LEGACY_FIELD_MAP.items():
            if canonical_key in data:
                data[legacy_key] = data[canonical_key]
        for field in list(data.keys()):
            if not field.startswith("v2_"):
                data[f"v2_{field}"] = data[field]

        SECRET_KEYS = {
            "coindcx_api_secret",
            "coindcx_live_api_secret",
            "coindcx_paper_api_secret",
            "coindcx_api_key",
            "coindcx_live_api_key",
            "zerodha_api_key",
            "zerodha_access_token",
            "dhan_client_id",
            "dhan_access_token",
            "alert_bot_token",
            "gemini_api_key",
            "operator_security_password",
        }
        for k, v in data.items():
            if k in SECRET_KEYS and v:
                data[k] = "***REDACTED***"
        return data

    def apply_override(self, override_path: str | None = None) -> AppConfig:
        """
        Return a copy of this config with hot-reloadable keys overridden
        from *override_path* (defaults to data/config_override.json).
        """
        HOT_RELOAD_KEYS = {
            "deployment_mode",
            "v2_deployment_mode",
            "shadow_mode",
            "v2_shadow_mode",
            "trading_enabled",
            "v2_trading_enabled",
            "execution_timeout",
            "v2_execution_timeout",
            "db_path",
            "v2_db_path",
            "scanner_poll_interval",
            "v2_scanner_poll_interval",
            "scanner_signal_ttl",
            "v2_scanner_signal_ttl",
            "metrics_snapshot_interval",
            "v2_metrics_snapshot_interval",
            "health_check_interval",
            "v2_health_check_interval",
            "ai_enabled",
            "v2_ai_enabled",
            "ai_model",
            "v2_ai_model",
            "ai_min_priority",
            "v2_ai_min_priority",
            "ai_confidence_threshold",
            "v2_ai_confidence_threshold",
            "ai_timeout_seconds",
            "v2_ai_timeout_seconds",
            "ai_max_retries",
            "v2_ai_max_retries",
            "alert_bot_token",
            "alert_chat_id",
            "order_size_inr",
            "total_capital_limit",
            "trading_capital_pool",
        }
        env_override = os.getenv("CONFIG_OVERRIDE_PATH")
        candidates = [
            Path(override_path) if override_path else None,
            Path(env_override) if env_override else None,
            Path("data/config_override.json"),
        ]
        target_path: Path | None = None
        for p in candidates:
            if p and p.exists():
                target_path = p
                break

        if not target_path or not target_path.exists():
            return self
        try:
            overrides = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            return self

        raw_updates = {k: v for k, v in overrides.items() if k in HOT_RELOAD_KEYS}
        updates: dict[str, Any] = {}
        for k, v in raw_updates.items():
            target_key = LEGACY_FIELD_MAP.get(k, k)
            if (
                target_key.startswith("v2_")
                and target_key[3:] in self.__class__.model_fields
            ):
                target_key = target_key[3:]
            updates[target_key] = v

        if "order_size_inr" in updates:
            updates["order_size_inr"] = max(200.0, float(updates["order_size_inr"]))
        if not updates:
            return self
        return self.model_copy(update=updates)

    @classmethod
    def save_runtime_overrides(
        cls, overrides: dict[str, Any], override_path: str | None = None
    ) -> AppConfig:
        """Persist runtime overrides to config_override.json and reload cache."""
        env_override = os.getenv("CONFIG_OVERRIDE_PATH")
        path = Path(override_path or env_override or "data/config_override.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        if "order_size_inr" in overrides:
            overrides["order_size_inr"] = max(200.0, float(overrides["order_size_inr"]))
        existing.update(overrides)
        if "order_size_inr" in existing:
            existing["order_size_inr"] = max(200.0, float(existing["order_size_inr"]))
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        invalidate_config()
        return cls().apply_override(override_path=str(path))


# Canonical alias for backward compatibility
# Backward compatibility alias (permanent, used by legacy tests)
V2Config = AppConfig


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """
    Return the singleton AppConfig instance with runtime overrides applied.

    Call invalidate_config() to force a reload (e.g. in tests).
    """
    cfg = AppConfig()
    return cfg.apply_override()


def invalidate_config() -> None:
    """Clear the cached config singleton (for tests and hot-reload)."""
    get_config.cache_clear()
