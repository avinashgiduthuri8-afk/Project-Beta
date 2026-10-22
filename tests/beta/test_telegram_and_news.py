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


@pytest.mark.asyncio
async def test_telegram_client_default_token_and_keyboards():
    client = TelegramClient()
    assert client.is_configured
    assert client.bot_token == "8399759111:AAFTDH54kYhmgkYF90Vt_fssOx9JDPKy6iQ"
    assert client.chat_id == "476738400"

    # Verify interactive menu keyboards
    kb = client._build_main_keyboard()
    assert "inline_keyboard" in kb
    button_texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
    assert "📊 Status" in button_texts
    assert "🎯 Signals" in button_texts
    assert "📈 Positions" in button_texts
    assert "💼 Balance" in button_texts
    assert "🛑 Emergency Stop" in button_texts

    back_kb = client._build_back_keyboard("cb:positions")
    assert "inline_keyboard" in back_kb
    assert back_kb["inline_keyboard"][0][0]["text"] == "🔙 Main Menu"

    # Watchlist compilation
    watchlist_text = await client._compile_watchlist_text()
    assert "NSE LIQUID WATCHLIST" in watchlist_text

    # Learner calibration notification
    unconfigured = TelegramClient(bot_token=None, chat_id=None, resolve_default_credentials=False)
    assert not await unconfigured.notify_learning_calibration("STE", 1.05, 88.0, 66.7, 9)

    await client.close()
    await unconfigured.close()
