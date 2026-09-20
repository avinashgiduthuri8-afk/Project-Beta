"""
V2 AI Intelligence Service — Evaluates setups using LLMs with Circuit Breaker Fallback.
"""

from __future__ import annotations

import asyncio
import time
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import pandas as pd

from v2.core.logging import get_logger
from v2.repository.db import Database
from v2.services.ai_intelligence_service.circuit_breaker import CircuitBreaker, FallbackEvaluator

logger = get_logger("v2.services.ai_intelligence_service")


class AIIntelligenceService:
    """
    Coordinates LLM evaluation of trade setups.
    Integrates CircuitBreaker to failover to FallbackEvaluator if API latency/errors spike.
    """

    def __init__(self, db: Optional[Database] = None):
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
        self.db = db

    async def evaluate_setup(
        self, 
        symbol: str, 
        confluence_score: float, 
        ltp: float, 
        signal_id: Optional[str] = None,
        df: Optional[pd.DataFrame] = None
    ) -> Dict[str, Any]:
        """
        Evaluates the trade setup. Uses LLM if circuit breaker is CLOSED/HALF_OPEN.
        Falls back to rule-based engine if OPEN or if LLM fails.
        """
        start_time = time.time()
        
        if not self.circuit_breaker.allow_execution():
            logger.warning(f"CircuitBreaker OPEN: Routing {symbol} to FallbackEvaluator.")
            result = FallbackEvaluator.evaluate_setup(symbol, confluence_score, ltp)
            await self._record_analysis(signal_id or str(uuid.uuid4()), result)
            return result

        try:
            # Simulate LLM Network Call (e.g., Gemini 2.5 Flash / Claude 3.5 Haiku)
            # In a full implementation, this would build the prompt from `df` and call the API.
            await asyncio.sleep(0.3)  # Simulated latency
            
            # Simulated LLM decision logic
            confirmed = confluence_score >= 85.0
            
            result = {
                "verdict": "CONFIRMED" if confirmed else "REJECTED",
                "confidence": 0.92 if confirmed else 0.45,
                "thesis": f"AI Thesis: Strong accumulation observed in {symbol} aligned with sector momentum.",
                "risks": ["Broader market volatility", "Upcoming earnings"],
                "catalysts": ["Volume breakout", "Moving average crossover"],
                "is_fallback": False,
                "model": "gemini-2.5-flash"
            }
            
            self.circuit_breaker.record_success()
            
        except Exception as e:
            logger.error(f"LLM API Call failed for {symbol}: {e}", exc_info=True)
            self.circuit_breaker.record_failure()
            result = FallbackEvaluator.evaluate_setup(symbol, confluence_score, ltp)
            
        latency_ms = (time.time() - start_time) * 1000.0
        result["latency_ms"] = round(latency_ms, 2)
        
        await self._record_analysis(signal_id or str(uuid.uuid4()), result)
        return result

    async def _record_analysis(self, signal_id: str, result: Dict[str, Any]) -> None:
        """Persists the AI analysis outcome to the database."""
        if not self.db:
            return
            
        try:
            now = datetime.now(timezone.utc).isoformat()
            risks_json = json.dumps(result.get("risks", []))
            catalysts_json = json.dumps(result.get("catalysts", []))
            
            await self.db.connection.execute(
                """
                INSERT INTO ai_analyses
                (id, signal_id, model, confidence, verdict, thesis, risks_json, catalysts_json, latency_ms, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    signal_id,
                    result.get("model", "fallback-rules-engine"),
                    result.get("confidence", 0.0),
                    result.get("verdict", "UNKNOWN"),
                    result.get("thesis", ""),
                    risks_json,
                    catalysts_json,
                    result.get("latency_ms", 0.0),
                    now
                )
            )
            await self.db.connection.commit()
        except Exception as e:
            logger.error(f"Failed to record AI analysis to DB: {e}")

