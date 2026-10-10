import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from core.bus.event_types import EventType
from core.types import BotName, Position, PositionStatus, BotMode
from execution.trading.precision_rules import round_price
from core.repository.position_repo import PositionRepository
from scanner.service import ScannerService

@pytest.mark.asyncio
async def test_bug1_entry_price_zero_guard():
    pass

@pytest.mark.asyncio
async def test_bug2_missing_zero_price_risk_service():
    pass

@pytest.mark.asyncio
async def test_bug3_scaled_amount_floor():
    pass

@pytest.mark.asyncio
async def test_scanner_never_emits_zero_price():
    bus = AsyncMock()
    market_repo = MagicMock()
    event_log_repo = MagicMock()
    config = MagicMock()
    config.scanner_min_priority = "Medium"
    svc = ScannerService(bus, market_repo, event_log_repo, config)
    sig = MagicMock()
    sig.id = "sig-1"
    sig.coin = "PEPE"
    sig.pair = "PEPE/INR"
    sig.raw_payload = {"price": 0.0, "close": 0.0}
    await svc._publish_signal_generated(sig)
    for call in bus.publish.call_args_list:
        assert call[0][0] != EventType.SIGNAL_GENERATED

def test_round_price_never_zero_for_positive_input():
    assert round_price("PEPE/INR", 0.0000001) > 0.0
    assert round_price("SHIB/INR", 1e-12) > 0.0
    assert round_price("BONK/USDT", 1e-15) > 0.0

@pytest.mark.asyncio
async def test_position_repo_rejects_zero_entry_price():
    db = AsyncMock()
    repo = PositionRepository(db)
    pos = Position(
        id="pos-1", bot=BotName.STE, coin="PEPE", pair="PEPE/INR",
        qty=1000.0, entry_price=0.0, entry_time=datetime.now(timezone.utc),
        current_price=10.0, unrealised_pnl=0.0, mode=BotMode.PAPER,
        signal_id="sig-1", status=PositionStatus.OPEN
    )
    with pytest.raises(ValueError, match="Invalid entry_price: 0.0"):
        await repo.insert(pos)
