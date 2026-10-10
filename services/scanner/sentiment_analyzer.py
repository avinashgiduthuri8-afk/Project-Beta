"""
PROJECT-ALPHA — Sentiment & Alternative Data Service (Skill 21).

Evaluates coin and market sentiment from headlines, exchange announcements, and alt-data feeds:
  1. Lexicon & Keyword Density Analysis (Weighted positive, negative, and catalyst scores)
  2. Delisting & Regulatory Investigation Risk Detection
  3. Normalized Sentiment Index (0 - 100 scale, where 50 is neutral)
  4. Sentiment Category Classification (VERY_BULLISH, BULLISH, NEUTRAL, BEARISH, VERY_BEARISH)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logging import get_logger

logger = get_logger("scanner.sentiment_analyzer")


class SentimentCategory(str, Enum):
    VERY_BULLISH = "VERY_BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    VERY_BEARISH = "VERY_BEARISH"


# Weighted keyword dictionaries
POSITIVE_KEYWORDS: dict[str, float] = {
    "partnership": 15.0,
    "mainnet": 12.0,
    "upgrade": 10.0,
    "integration": 10.0,
    "adoption": 10.0,
    "etf approved": 25.0,
    "institutional": 15.0,
    "all-time high": 15.0,
    "ath": 10.0,
    "buyback": 12.0,
    "burn": 10.0,
    "staking": 8.0,
    "listing": 10.0,
    "bullish": 8.0,
    "breakout": 10.0,
    "rally": 8.0,
    "record high": 15.0,
}

NEGATIVE_KEYWORDS: dict[str, float] = {
    "hack": -25.0,
    "hacked": -25.0,
    "exploit": -25.0,
    "exploited": -25.0,
    "sec": -15.0,
    "lawsuit": -15.0,
    "investigation": -15.0,
    "sued": -15.0,
    "rugpull": -30.0,
    "scam": -30.0,
    "bankruptcy": -35.0,
    "insolvent": -35.0,
    "insolvency": -35.0,
    "fraud": -30.0,
    "stolen": -20.0,
    "vulnerability": -15.0,
    "subpoena": -15.0,
    "criminal": -20.0,
    "bearish": -8.0,
    "dump": -12.0,
    "crash": -15.0,
}

CRITICAL_DELISTING_KEYWORDS: list[str] = [
    "delist",
    "delisting",
    "remove pair",
    "halt trading",
    "cease support",
    "trading suspended",
    "suspending trading",
    "delisted",
]


@dataclass
class CoinSentimentResult:
    """Structured sentiment evaluation result."""

    coin: str
    sentiment_score: float  # 0.0 - 100.0 (50.0 is baseline neutral)
    category: SentimentCategory
    headline_count: int
    delisting_risk: bool
    negative_event_detected: bool
    positive_event_detected: bool
    matched_keywords: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class SentimentAnalyzer:
    """
    Analyzes headlines and alternative news feeds for CoinDCX tradeable assets.
    """

    def __init__(self, neutral_score: float = 50.0) -> None:
        self.neutral_score = neutral_score

    def analyze_headlines(
        self, coin: str, headlines: list[str | dict[str, Any]]
    ) -> CoinSentimentResult:
        """
        Calculates sentiment index (0-100) and detects high-risk headlines.
        """
        if not headlines:
            return CoinSentimentResult(
                coin=coin.upper(),
                sentiment_score=self.neutral_score,
                category=SentimentCategory.NEUTRAL,
                headline_count=0,
                delisting_risk=False,
                negative_event_detected=False,
                positive_event_detected=False,
                details={"message": "No news headlines provided"},
            )

        extracted_text: list[str] = []
        for item in headlines:
            if isinstance(item, str):
                extracted_text.append(item)
            elif isinstance(item, dict):
                text = item.get("title") or item.get("headline") or item.get("text") or ""
                if text:
                    extracted_text.append(str(text))

        if not extracted_text:
            return CoinSentimentResult(
                coin=coin.upper(),
                sentiment_score=self.neutral_score,
                category=SentimentCategory.NEUTRAL,
                headline_count=0,
                delisting_risk=False,
                negative_event_detected=False,
                positive_event_detected=False,
            )

        delisting_flag = False
        negative_flag = False
        positive_flag = False
        matched_keywords: list[str] = []
        reasons: list[str] = []

        total_adjustment = 0.0

        for text in extracted_text:
            lower_text = text.lower()

            # 1. Delisting Risk Check
            for kw in CRITICAL_DELISTING_KEYWORDS:
                if kw in lower_text:
                    delisting_flag = True
                    matched_keywords.append(f"delisting:{kw}")
                    reasons.append(f"Delisting keyword detected: '{kw}' in '{text[:60]}...'")

            # 2. Positive Keyword Matching
            for kw, weight in POSITIVE_KEYWORDS.items():
                if kw in lower_text:
                    total_adjustment += weight
                    positive_flag = True
                    matched_keywords.append(f"pos:{kw}")

            # 3. Negative Keyword Matching
            for kw, weight in NEGATIVE_KEYWORDS.items():
                if kw in lower_text:
                    total_adjustment += weight  # weight is negative
                    negative_flag = True
                    matched_keywords.append(f"neg:{kw}")
                    reasons.append(f"Negative risk event detected: '{kw}'")

        # Normalize score into [0.0, 100.0]
        if delisting_flag:
            final_score = 0.0
        else:
            final_score = max(0.0, min(100.0, self.neutral_score + total_adjustment))

        # Classify category
        if final_score >= 80.0:
            category = SentimentCategory.VERY_BULLISH
        elif final_score >= 60.0:
            category = SentimentCategory.BULLISH
        elif final_score <= 20.0:
            category = SentimentCategory.VERY_BEARISH
        elif final_score <= 40.0:
            category = SentimentCategory.BEARISH
        else:
            category = SentimentCategory.NEUTRAL

        return CoinSentimentResult(
            coin=coin.upper(),
            sentiment_score=round(final_score, 1),
            category=category,
            headline_count=len(extracted_text),
            delisting_risk=delisting_flag,
            negative_event_detected=negative_flag,
            positive_event_detected=positive_flag,
            matched_keywords=list(set(matched_keywords)),
            rejection_reasons=reasons,
            details={
                "raw_adjustment": total_adjustment,
                "headlines_evaluated": len(extracted_text),
            },
        )

