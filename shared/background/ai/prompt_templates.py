"""
AI Intelligence Prompt Templates & Schema Builders.

Provides structured prompt generation and JSON schemas for LLM-based
crypto signal confirmation.
"""

from __future__ import annotations

import json

from core.types import Signal

AI_EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "string",
            "enum": ["APPROVE", "REJECT", "SCALE_DOWN", "WATCH"],
            "description": "Trading recommendation: APPROVE (take trade), REJECT (veto trade), SCALE_DOWN (take trade with reduced risk), WATCH (not ready yet)",
        },
        "confidence_score": {
            "type": "integer",
            "description": "Confidence rating from 0 to 100",
            "minimum": 0,
            "maximum": 100,
        },
        "trend_evaluation": {
            "type": "string",
            "description": "Evaluation of multi-timeframe trend alignment and momentum strength",
        },
        "momentum_evaluation": {
            "type": "string",
            "description": "Evaluation of RSI, MACD, oscillators, and momentum exhaustion",
        },
        "volume_evaluation": {
            "type": "string",
            "description": "Evaluation of volume expansion, volume spike ratio, and liquidity",
        },
        "setup_quality": {
            "type": "string",
            "description": "Quality of the pattern / setup archetype (STE momentum, HDA absorption, MTB breakout, etc.)",
        },
        "market_regime": {
            "type": "string",
            "description": "Identified market regime (bull_trend, range_bound, high_volatility, bear_trend)",
        },
        "risk_reward_assessment": {
            "type": "string",
            "description": "Estimated risk-to-reward ratio and key price hurdle assessment",
        },
        "supporting_factors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Bullet points supporting the trade",
        },
        "conflicts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Contradictions, divergence, or opposing indicators",
        },
        "risk_factors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Identified risks (overhead resistance, low liquidity, high volatility)",
        },
        "suggested_adjustments": {
            "type": "object",
            "properties": {
                "size_multiplier": {
                    "type": "number",
                    "description": "Position size multiplier (0.1 to 1.5, default 1.0)",
                },
                "tighten_stop": {
                    "type": "boolean",
                    "description": "Whether stop loss should be placed tighter than default",
                },
                "target_notes": {
                    "type": "string",
                    "description": "Suggested take-profit target rationale",
                },
            },
            "required": ["size_multiplier", "tighten_stop"],
        },
    },
    "required": [
        "recommendation",
        "confidence_score",
        "trend_evaluation",
        "momentum_evaluation",
        "volume_evaluation",
        "setup_quality",
        "market_regime",
        "risk_reward_assessment",
        "supporting_factors",
        "conflicts",
        "risk_factors",
        "suggested_adjustments",
    ],
}


SYSTEM_INSTRUCTION = """
You are the Senior AI Quantitative Risk & Strategy Intelligence Engine for PROJECT-ALPHA (Crypto Trading Platform).
Your role is to act as an independent confirmation filter on candidate trading signals produced by the Market Scanner.

Core Principles:
1. Protect capital first. Reject ambiguous, late, or over-extended setups.
2. Confirm multi-timeframe trend alignment, momentum acceleration, and volume participation.
3. Check for false breakouts, bearish divergence, resistance overhead, and abnormal volatility risks.
4. Output MUST conform strictly to the specified JSON schema.
"""


def build_signal_prompt(signal: Signal) -> str:
    """Build a structured analysis prompt from a domain Signal."""
    raw = signal.raw_payload or {}

    context = {
        "signal_id": signal.id,
        "coin": signal.coin,
        "pair": signal.pair,
        "market_state": signal.market_state.value,
        "opportunity_type": signal.opportunity_type.value,
        "scanner_priority": signal.priority.value,
        "scanner_risk_level": signal.risk_level.value,
        "scanner_score": signal.score,
        "scanner_confidence": signal.confidence,
        "coin_class": signal.coin_class or "Standard",
        "mtf_alignment": signal.mtf_alignment,
        "indicators": {
            "price": raw.get("price") or raw.get("close"),
            "change_24h_pct": raw.get("change_24h_pct") or raw.get("pct_change_24h"),
            "volume_24h": raw.get("volume_24h") or raw.get("volume"),
            "volume_spike_ratio": raw.get("volume_spike_ratio")
            or raw.get("volume_spike"),
            "rsi_14": raw.get("rsi") or raw.get("rsi_14"),
            "macd": raw.get("macd"),
            "macd_signal": raw.get("macd_signal"),
            "macd_hist": raw.get("macd_hist"),
            "atr": raw.get("atr"),
            "supertrend": raw.get("supertrend"),
            "timeframe_15m": raw.get("tf_15m") or raw.get("15m"),
            "timeframe_1h": raw.get("tf_1h") or raw.get("1h"),
            "timeframe_24h": raw.get("tf_24h") or raw.get("24h"),
        },
        "raw_metadata": {
            k: v for k, v in raw.items() if k not in ("indicators", "candles")
        },
    }

    return f"""
Please evaluate the following crypto market candidate signal for entry confirmation:

```json
{json.dumps(context, indent=2, default=str)}
```

Analyze the setup, trend conviction, volume profile, indicator alignment, and risk-reward profile.
Return a structured JSON evaluation adhering to the schema.
"""
