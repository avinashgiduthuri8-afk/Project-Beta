"""
V2 AIAnalysisRepository — persistence for AI signal evaluations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import aiosqlite

from core.logging import get_logger
from core.types import AIAnalysis, AIRecommendation

from .base import BaseRepository

logger = get_logger("core.repository.ai_repo")

_ISO = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def _dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    for fmt in (
        _ISO,
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _row_to_ai_analysis(row: aiosqlite.Row) -> AIAnalysis:
    d = dict(row)
    supporting = BaseRepository._loads(d.get("supporting_factors")) or []
    conflicts = BaseRepository._loads(d.get("conflicts")) or []
    risks = BaseRepository._loads(d.get("risk_factors")) or []
    adjustments = BaseRepository._loads(d.get("suggested_adjustments")) or {}
    raw_resp = BaseRepository._loads(d.get("raw_response")) or {}

    return AIAnalysis(
        id=d["id"],
        signal_id=d["signal_id"],
        coin=d["coin"],
        pair=d["pair"],
        recommendation=AIRecommendation(d["recommendation"]),
        confidence_score=int(d["confidence_score"]),
        trend_evaluation=d.get("trend_evaluation", ""),
        momentum_evaluation=d.get("momentum_evaluation", ""),
        volume_evaluation=d.get("volume_evaluation", ""),
        setup_quality=d.get("setup_quality", ""),
        market_regime=d.get("market_regime", ""),
        risk_reward_assessment=d.get("risk_reward_assessment", ""),
        supporting_factors=supporting if isinstance(supporting, list) else [],
        conflicts=conflicts if isinstance(conflicts, list) else [],
        risk_factors=risks if isinstance(risks, list) else [],
        suggested_adjustments=adjustments if isinstance(adjustments, dict) else {},
        model_name=d.get("model_name", "unknown"),
        execution_latency_ms=float(d.get("execution_latency_ms", 0.0)),
        analyzed_at=_dt(d["analyzed_at"]) or datetime.now(timezone.utc),
        raw_response=raw_resp if isinstance(raw_resp, dict) else {},
    )


class AIAnalysisRepository(BaseRepository):
    """Persistence operations for AI analysis records."""

    async def insert(self, analysis: AIAnalysis) -> str:
        """Persist a new AI evaluation. Returns analysis id."""
        if not analysis.id:
            analysis.id = str(uuid.uuid4())

        await self._execute(
            """
            INSERT OR REPLACE INTO ai_analyses
            (id, signal_id, coin, pair, recommendation, confidence_score,
             trend_evaluation, momentum_evaluation, volume_evaluation,
             setup_quality, market_regime, risk_reward_assessment,
             supporting_factors, conflicts, risk_factors, suggested_adjustments,
             model_name, execution_latency_ms, analyzed_at, raw_response)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                analysis.id,
                analysis.signal_id,
                analysis.coin,
                analysis.pair,
                analysis.recommendation.value,
                analysis.confidence_score,
                analysis.trend_evaluation,
                analysis.momentum_evaluation,
                analysis.volume_evaluation,
                analysis.setup_quality,
                analysis.market_regime,
                analysis.risk_reward_assessment,
                self._dumps(analysis.supporting_factors),
                self._dumps(analysis.conflicts),
                self._dumps(analysis.risk_factors),
                self._dumps(analysis.suggested_adjustments),
                analysis.model_name,
                analysis.execution_latency_ms,
                analysis.analyzed_at.isoformat(),
                self._dumps(analysis.raw_response),
            ),
        )
        return analysis.id

    async def get_by_id(self, analysis_id: str) -> AIAnalysis | None:
        row = await self._fetchone(
            "SELECT * FROM ai_analyses WHERE id=?", (analysis_id,)
        )
        return _row_to_ai_analysis(row) if row else None

    async def get_by_signal_id(self, signal_id: str) -> AIAnalysis | None:
        row = await self._fetchone(
            "SELECT * FROM ai_analyses WHERE signal_id=? ORDER BY analyzed_at DESC LIMIT 1",
            (signal_id,),
        )
        return _row_to_ai_analysis(row) if row else None

    async def get_recent(
        self,
        limit: int = 50,
        recommendation: str | None = None,
        min_confidence: int = 0,
    ) -> list[AIAnalysis]:
        """Return most recent AI analyses with optional filters."""
        clauses = ["confidence_score >= ?"]
        params: list[object] = [min_confidence]

        if recommendation:
            clauses.append("recommendation = ?")
            params.append(recommendation)

        where_str = " AND ".join(clauses)
        sql = f"SELECT * FROM ai_analyses WHERE {where_str} ORDER BY analyzed_at DESC LIMIT ?"
        params.append(limit)

        rows = await self._fetchall(sql, tuple(params))
        return [_row_to_ai_analysis(r) for r in rows]

    async def get_by_coin(self, coin: str, limit: int = 20) -> list[AIAnalysis]:
        rows = await self._fetchall(
            "SELECT * FROM ai_analyses WHERE coin=? ORDER BY analyzed_at DESC LIMIT ?",
            (coin, limit),
        )
        return [_row_to_ai_analysis(r) for r in rows]

    async def count_by_recommendation(self) -> dict[str, int]:
        rows = await self._fetchall(
            "SELECT recommendation, COUNT(*) as n FROM ai_analyses GROUP BY recommendation"
        )
        return {r["recommendation"]: r["n"] for r in rows}
