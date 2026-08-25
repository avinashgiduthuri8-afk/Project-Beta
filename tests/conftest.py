"""Pytest fixtures and configuration."""

import pytest
import tempfile
from pathlib import Path
from config.config_loader import AppConfig
from brokers.paper_broker import PaperBroker
from oms.order_manager import OrderManager
from oms.execution_router import ExecutionRouter
from data.event_bus import EventBus


@pytest.fixture
def mock_app_config():
    config = AppConfig()
    config.trading.mode = "paper"
    config.trading.broker = "paper"
    return config


@pytest.fixture
def paper_broker():
    return PaperBroker(initial_capital=100000.0, slippage_pct=0.0)


@pytest.fixture
def order_manager():
    return OrderManager()


@pytest.fixture
def execution_router(paper_broker):
    return ExecutionRouter(broker=paper_broker, default_tick_size=0.05)


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def temp_cache_file():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)
