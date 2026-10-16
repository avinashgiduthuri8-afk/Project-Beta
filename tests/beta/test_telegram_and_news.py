"""Tests for Telegram notifications and 30-day News Sentiment scraper."""

import pytest

from shared.telegram.telegram_client import TelegramClient
from shared.market.news_service import NewsSentimentService


@pytest.mark.asyncio
async def test_telegram_client_handles_unconfigured_gracefully():
    client = TelegramClient(bot_token=None, chat_id=None, resolve_default_credentials=False)
    assert not client.is_configured
    assert not await client.send_message("Test message")
    assert not await client.notify_runtime_start()
    assert not await client.notify_scanner_signal("NSE:NMDC", "NR7", 70.8, 90)
    assert not await client.notify_paper_buy("NSE:NMDC", 19, 70.8, 1345.2, 77.2, 68.3, "NR7")
    assert not await client.notify_paper_exit("NSE:NMDC", 19, 70.8, 77.2, 121.6, 117.1, 9.04, "TARGET_HIT")
    assert not await client.notify_portfolio_status(13654.8, 1345.2, 1, 117.1)
    await client.close()


@pytest.mark.asyncio
async def test_telegram_client_configured_properties():
    client = TelegramClient(bot_token="test_token", chat_id="123456")
    assert client.is_configured
    assert client.bot_token == "test_token"
    assert client.chat_id == "123456"
    await client.close()


def test_news_sentiment_scoring_rules():
    service = NewsSentimentService()

    # Severe negative headlines trigger HIGH_RISK
    neg_headlines = [
        "SEBI imposes penalty on company over compliance failure",
        "ED raid conducted at company headquarters regarding scam",
        "Company reports severe loss widen and default on debt payment",
    ]
    status, score, flags, top = service._score_headlines(neg_headlines)
    assert status == "HIGH_RISK"
    assert score <= -4
    assert len(flags) >= 2

    # Positive headlines trigger POSITIVE
    pos_headlines = [
        "Company secures massive ₹1,500 Cr order win for expansion",
        "Profit surges 35% with strong quarterly growth and dividend declared",
    ]
    status, score, flags, top = service._score_headlines(pos_headlines)
    assert status == "POSITIVE"
    assert score > 0
    assert len(flags) == 0

    # Missing headlines are unknown and must not silently become safe.
    status, score, flags, top = service._score_headlines([])
    assert status == "UNKNOWN"
    assert score == 0
