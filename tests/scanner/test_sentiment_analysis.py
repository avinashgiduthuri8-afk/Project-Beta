"""
PROJECT-ALPHA — Unit Tests for Sentiment & Alternative Data Service (Skill 21).
"""

from __future__ import annotations

import pytest

from scanner.sentiment_analyzer import (
    CoinSentimentResult,
    SentimentAnalyzer,
    SentimentCategory,
)


def test_sentiment_analyzer_neutral_empty_news():
    analyzer = SentimentAnalyzer()
    res = analyzer.analyze_headlines("BTC", [])

    assert res.coin == "BTC"
    assert res.sentiment_score == 50.0
    assert res.category == SentimentCategory.NEUTRAL
    assert res.delisting_risk is False
    assert res.negative_event_detected is False


def test_sentiment_analyzer_bullish_catalyst():
    analyzer = SentimentAnalyzer()
    headlines = [
        "Major institutional partnership announced for SOL ecosystem",
        "SOL mainnet upgrade completes successfully with record high TPS",
    ]
    res = analyzer.analyze_headlines("SOL", headlines)

    assert res.sentiment_score > 60.0
    assert res.category in (SentimentCategory.BULLISH, SentimentCategory.VERY_BULLISH)
    assert res.positive_event_detected is True
    assert res.delisting_risk is False


def test_sentiment_analyzer_delisting_risk():
    analyzer = SentimentAnalyzer()
    headlines = [
        "Exchange announcement: Binance to delist trading pair for XYZ token next week",
    ]
    res = analyzer.analyze_headlines("XYZ", headlines)

    assert res.sentiment_score == 0.0
    assert res.category == SentimentCategory.VERY_BEARISH
    assert res.delisting_risk is True
    assert any("delist" in r.lower() for r in res.rejection_reasons)


def test_sentiment_analyzer_negative_hack_event():
    analyzer = SentimentAnalyzer()
    headlines = [
        "DeFi protocol suffers $50M exploit due to smart contract vulnerability",
        "SEC opens investigation into token issuer",
    ]
    res = analyzer.analyze_headlines("DEF", headlines)

    assert res.sentiment_score < 30.0
    assert res.negative_event_detected is True
    assert res.category in (SentimentCategory.BEARISH, SentimentCategory.VERY_BEARISH)
