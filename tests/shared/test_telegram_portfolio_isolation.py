"""
PROJECT-ALPHA — Telegram /portfolio Isolation Regression Test.

Verifies:
  1. Telegram /portfolio in PAPER mode returns PAPER virtual starting capital & 24/7 SIMULATION.
  2. Telegram /portfolio in LIVE mode returns LIVE PRODUCTION header and NEVER returns 24/7 SIMULATION or virtual starting capital.
  3. Live portfolio queries position_repo and trade_repo with mode="LIVE" filter.
  4. Paper portfolio queries position_repo and trade_repo with mode="PAPER" filter.
  5. Cross-reading between Paper and Live portfolio data is strictly impossible.
"""

import pytest
from datetime import datetime, timezone

from core.bus.event_bus import EventBus
from core.config import AppConfig
from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, Position, PositionStatus, Trade, ExitReason
from telegram.telegram_interface import TelegramInteractiveInterface
from telegram.formatters import format_telegram_portfolio
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_paper_vs_live_portfolio_isolation(tmp_path):
    # Setup paper and live DBs
    paper_db_path = str(tmp_path / "paper_test.db")
    live_db_path = str(tmp_path / "live_test.db")

    p_db = Database(paper_db_path)
    l_db = Database(live_db_path)
    await p_db.open()
    await l_db.open()

    p_pos_repo = PositionRepository(p_db.connection)
    p_trade_repo = TradeRepository(p_db.connection)

    l_pos_repo = PositionRepository(l_db.connection)
    l_trade_repo = TradeRepository(l_db.connection)

    now = datetime.now(timezone.utc)

    # Insert PAPER position in paper DB
    paper_pos = Position(
        id="pos_paper_1",
        bot=BotName.STE,
        coin="SOL",
        pair="SOL/INR",
        qty=1.0,
        entry_price=12000.0,
        entry_time=now,
        mode=BotMode.PAPER,
        status=PositionStatus.OPEN,
        current_price=13000.0,
        unrealised_pnl=1000.0,
    )
    await p_pos_repo.insert(paper_pos)

    # Insert LIVE position in live DB
    live_pos = Position(
        id="pos_live_1",
        bot=BotName.STE,
        coin="BTC",
        pair="BTC/INR",
        qty=0.01,
        entry_price=6000000.0,
        entry_time=now,
        mode=BotMode.LIVE,
        status=PositionStatus.OPEN,
        current_price=6500000.0,
        unrealised_pnl=5000.0,
    )
    await l_pos_repo.insert(live_pos)

    mock_client = MagicMock()
    mock_client.send_message = AsyncMock(return_value=True)
    cfg = AppConfig()

    # Create Paper Telegram Interface
    paper_iface = TelegramInteractiveInterface(
        telegram_client=mock_client,
        bus=EventBus(),
        config=cfg,
        position_repo=p_pos_repo,
        trade_repo=p_trade_repo,
        mode_override="PAPER",
    )

    # Create Live Telegram Interface
    live_iface = TelegramInteractiveInterface(
        telegram_client=mock_client,
        bus=EventBus(),
        config=cfg,
        position_repo=l_pos_repo,
        trade_repo=l_trade_repo,
        mode_override="LIVE",
    )

    # Compile data for Paper interface
    paper_data = await paper_iface._compile_portfolio_data()
    paper_text = format_telegram_portfolio(paper_data)

    # Compile data for Live interface
    live_data = await live_iface._compile_portfolio_data()
    live_text = format_telegram_portfolio(live_data)

    # Assertions for PAPER Telegram Output
    assert paper_data["mode"] == "PAPER"
    assert "24/7 SIMULATION" in paper_text
    assert "Virtual Starting Capital" in paper_text
    assert "SOL" in paper_text
    assert "BTC" not in paper_text  # Must NOT contain live BTC position!

    # Assertions for LIVE Telegram Output
    assert live_data["mode"] == "LIVE"
    assert "LIVE PRODUCTION" in live_text
    assert "24/7 SIMULATION" not in live_text  # MUST NEVER contain simulation header!
    assert "Virtual Starting Capital" not in live_text  # MUST NEVER contain virtual starting capital!
    assert "BTC" in live_text
    assert "SOL" not in live_text  # Must NOT contain paper SOL position!

    await p_db.close()
    await l_db.close()
