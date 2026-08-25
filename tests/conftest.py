"""
Shared pytest fixtures for Project-Beta test suite.
"""

from __future__ import annotations

import pytest
from config.config_loader import BotSettings, load_settings
from brokers.paper_broker import PaperBroker
from data.event_bus import EventBus
from risk.market_clock import MarketClock
from risk.risk_engine import RiskEngine
from oms.order_manager import OrderManager


@pytest.fixture
def mock_settings() -> BotSettings:
    settings = load_settings()
    settings.env.trading_mode = "PAPER"
    settings.env.active_broker = "PAPER"
    settings.storage.session_cache_path = ".test_session_cache.json"
    settings.storage.db_path = "data/test_project_beta.db"
    settings.storage.journal_csv_path = "data/test_trade_journal.csv"
    return settings


@pytest.fixture
def paper_broker() -> PaperBroker:
    return PaperBroker(initial_capital=100000.0)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def risk_engine(mock_settings: BotSettings) -> RiskEngine:
    return RiskEngine(mock_settings)


@pytest.fixture
def order_manager(paper_broker: PaperBroker, mock_settings: BotSettings) -> OrderManager:
    return OrderManager(broker=paper_broker, settings=mock_settings)
