"""
Gemini REST API Client for AI Signal Intelligence.

Interacts with the Google Gemini API using structured JSON output mode,
schema validation, timeout enforcement, and retry mechanisms.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

import httpx

from core.logging import get_logger
from core.types import AIAnalysis, AIRecommendation, Signal

from .prompt_templates import (
    AI_EVALUATION_SCHEMA,
    SYSTEM_INSTRUCTION,
    build_signal_prompt,
)

logger = get_logger("background.ai.client")

_GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class GeminiClient:
    """Async Gemini client specialized for crypto quantitative signal evaluation."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def evaluate_signal(self, signal: Signal) -> AIAnalysis:
        """Call Gemini API with structured prompt and return verified AIAnalysis."""
        t0 = time.perf_counter()
        prompt_text = build_signal_prompt(signal)

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt_text}],
                }
            ],
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": AI_EVALUATION_SCHEMA,
                "temperature": 0.1,
            },
        }

        url = _GEMINI_API_URL.format(model=self.model)
        params = {"key": self.api_key}

        last_exc: Exception | None = None

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = await client.post(url, params=params, json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                    # Extract generated text from candidate response
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise ValueError(f"No candidates in Gemini response: {data}")

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts or "text" not in parts[0]:
                        raise ValueError(
                            f"Invalid candidate parts structure: {candidates[0]}"
                        )

                    raw_text = parts[0]["text"]
                    parsed = json.loads(raw_text)

                    latency_ms = (time.perf_counter() - t0) * 1000.0

                    rec_str = parsed.get("recommendation", "WATCH").upper()
                    try:
                        rec = AIRecommendation(rec_str)
                    except ValueError:
                        rec = AIRecommendation.WATCH

                    confidence = int(parsed.get("confidence_score", 50))
                    confidence = max(0, min(100, confidence))

                    supporting = parsed.get("supporting_factors") or []
                    conflicts = parsed.get("conflicts") or []
                    risks = parsed.get("risk_factors") or []
                    adjustments = parsed.get("suggested_adjustments") or {}

                    return AIAnalysis(
                        id=str(uuid.uuid4()),
                        signal_id=signal.id,
                        coin=signal.coin,
                        pair=signal.pair,
                        recommendation=rec,
                        confidence_score=confidence,
                        trend_evaluation=str(parsed.get("trend_evaluation", "")),
                        momentum_evaluation=str(parsed.get("momentum_evaluation", "")),
                        volume_evaluation=str(parsed.get("volume_evaluation", "")),
                        setup_quality=str(parsed.get("setup_quality", "")),
                        market_regime=str(parsed.get("market_regime", "")),
                        risk_reward_assessment=str(
                            parsed.get("risk_reward_assessment", "")
                        ),
                        supporting_factors=(
                            supporting if isinstance(supporting, list) else []
                        ),
                        conflicts=conflicts if isinstance(conflicts, list) else [],
                        risk_factors=risks if isinstance(risks, list) else [],
                        suggested_adjustments=(
                            adjustments if isinstance(adjustments, dict) else {}
                        ),
                        model_name=self.model,
                        execution_latency_ms=round(latency_ms, 2),
                        analyzed_at=datetime.now(timezone.utc),
                        raw_response=parsed,
                    )
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Gemini API evaluation attempt failed",
                        extra={
                            "coin": signal.coin,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                    )
                    if attempt < self.max_retries:
                        await httpx.AsyncClient().aclose()  # yield control

        raise last_exc or RuntimeError("Gemini API call failed after retries")
