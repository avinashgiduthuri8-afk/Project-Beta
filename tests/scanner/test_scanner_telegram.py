"""
Unit test for ScannerTelegramNotifier and signal alert formatting.
"""

import pytest
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from scanner.telegram.notifier import ScannerTelegramNotifier, format_scanner_signal_alert


def test_format_scanner_signal_alert():
    payload = {
        "coin": "SOL",
        "pair": "SOL/USDT",
        "strategy": "HDA",
        "score": 92,
        "confluence_score": 92,
        "price": 150.0,
        "take_profit": 165.0,
        "stop_loss": 142.5,
        "signal_reason": "High Delivery Absorption volume surge",
        "timeframe": "1h",
    }
    text = format_scanner_signal_alert(payload)
    assert "SOL" in text
    assert "SOL/USDT" in text
    assert "HDA" in text
    assert "92/100" in text
    assert "+10.0%" in text
    assert "-5.0%" in text
    assert "Forward Tracking" in text


@pytest.mark.asyncio
async def test_scanner_telegram_notifier_lifecycle():
    bus = EventBus()
    config = AppConfig()
    config.scanner_bot_token = None  # Mock unconfigured
    notifier = ScannerTelegramNotifier(bus=bus, config=config)

    await notifier.start()

    # Publishing a signal event should not raise errors even if unconfigured
    await bus.publish(
        EventType.SIGNAL_GENERATED,
        {
            "coin": "BTC",
            "pair": "BTC/USDT",
            "price": 65000.0,
            "strategy": "STE",
        },
    )

    await notifier.stop()
