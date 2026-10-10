"""
V2 Learning & Mistake Identification Engine.

Analyzes post-trade journal entries from JournalRepository to extract mistake patterns:
  - Consecutive Loss Clustering (3+ losses on pair/strategy)
  - MAE Excursion Leakage (> 2.0% MAE before exit)
  - Low MFE Capture Ratio (< 30% MFE efficiency)
  - Regime Incompatibility (strategy underperformance in BEARISH/RISK_OFF)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.logging import get_logger
from core.repository.journal_repo import JournalRepository
from core.repository.learning_repo import LearningRepository

logger = get_logger("background.learning.engine")


class LearningEngine:
    """Post-Trade Pattern Extraction & Mistake Diagnosis Engine."""

    def __init__(
        self,
        journal_repo: JournalRepository,
        learning_repo: LearningRepository,
    ) -> None:
        self._journal_repo = journal_repo
        self._learning_repo = learning_repo

    async def analyze_trades_and_extract_insights(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Run mistake pattern detection across recent trade journal entries.
        Returns list of newly generated learning insight dictionaries.
        """
        entries = await self._journal_repo.get_entries(limit=limit, offset=0)
        if not entries:
            logger.info(
                "No trade journal entries available for learning engine analysis"
            )
            return []

        insights: list[dict[str, Any]] = []

        # 1. Consecutive Loss Clustering Detection
        loss_insights = self._detect_consecutive_losses(entries)
        insights.extend(loss_insights)

        # 2. MAE Excursion Leakage Detection
        mae_insights = self._detect_mae_excursions(entries)
        insights.extend(mae_insights)

        # 3. Low MFE Capture Efficiency Detection
        mfe_insights = self._detect_low_mfe_efficiency(entries)
        insights.extend(mfe_insights)

        # 4. Regime Incompatibility Detection
        regime_insights = self._detect_regime_mismatch(entries)
        insights.extend(regime_insights)

        # Persist detected insights to LearningRepository
        for insight in insights:
            await self._learning_repo.record_insight(insight)

        logger.info(
            "LearningEngine generated %d insight(s) from %d trades",
            len(insights),
            len(entries),
        )
        return insights

    def _detect_consecutive_losses(
        self, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Detect 3+ consecutive losing trades grouped by bot or pair."""
        insights: list[dict[str, Any]] = []
        now_str = datetime.now(timezone.utc).isoformat()

        # Group chronologically ascending
        sorted_entries = sorted(entries, key=lambda x: str(x.get("exit_timestamp", "")))

        # Group by bot_name
        by_bot: dict[str, list[dict[str, Any]]] = {}
        for e in sorted_entries:
            bot = str(e.get("bot_name", "STE")).upper()
            by_bot.setdefault(bot, []).append(e)

        for bot_name, bot_trades in by_bot.items():
            consecutive_losses = 0
            loss_pairs: list[str] = []
            for t in bot_trades:
                net_pnl = float(t.get("net_pnl", 0.0))
                if net_pnl < 0:
                    consecutive_losses += 1
                    loss_pairs.append(str(t.get("pair", "UNKNOWN")))
                else:
                    consecutive_losses = 0
                    loss_pairs.clear()

                if consecutive_losses >= 3:
                    last_pair = loss_pairs[-1] if loss_pairs else "GENERAL"
                    insights.append(
                        {
                            "id": str(uuid.uuid4()),
                            "bot_name": bot_name,
                            "pair": last_pair,
                            "pattern_type": "CONSECUTIVE_LOSSES",
                            "severity": (
                                "HIGH" if consecutive_losses == 3 else "CRITICAL"
                            ),
                            "lesson_summary": f"Detected {consecutive_losses} consecutive stop-outs for strategy {bot_name} on {last_pair}.",
                            "recommended_adjustment": f"Initiate COOLING_DOWN status for {bot_name}, tighten confluence threshold to 90.0, and reduce multiplier to 0.5x.",
                            "created_at": now_str,
                        }
                    )

        return insights

    def _detect_mae_excursions(
        self, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Detect trades where Maximum Adverse Excursion (MAE) was excessively large (>2% of entry notional)."""
        insights: list[dict[str, Any]] = []
        now_str = datetime.now(timezone.utc).isoformat()

        for e in entries:
            entry_price = float(e.get("entry_price", 100.0))
            qty = float(e.get("quantity", 1.0))
            mae = float(e.get("mae", 0.0) or 0.0)
            entry_notional = entry_price * qty

            if entry_notional > 0:
                mae_pct = (mae / entry_notional) * 100.0
                if mae_pct > 2.0:
                    bot_name = str(e.get("bot_name", "STE")).upper()
                    pair = str(e.get("pair", "BTC/INR")).upper()
                    insights.append(
                        {
                            "id": str(uuid.uuid4()),
                            "bot_name": bot_name,
                            "pair": pair,
                            "pattern_type": "MAE_EXCURSION_LEAK",
                            "severity": "MEDIUM" if mae_pct < 4.0 else "HIGH",
                            "lesson_summary": f"Excessive MAE excursion ({mae_pct:.2f}%) on {pair} before trade completion.",
                            "recommended_adjustment": f"Tighten entry trigger timing and require confirmation candle for strategy {bot_name} on {pair}.",
                            "created_at": now_str,
                        }
                    )

        return insights

    def _detect_low_mfe_efficiency(
        self, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Detect winning trades that captured less than 30% of potential Maximum Favorable Excursion (MFE)."""
        insights: list[dict[str, Any]] = []
        now_str = datetime.now(timezone.utc).isoformat()

        for e in entries:
            net_pnl = float(e.get("net_pnl", 0.0))
            mfe = float(e.get("mfe", 0.0) or 0.0)

            if net_pnl > 0 and mfe > 0:
                capture_ratio = net_pnl / mfe
                if capture_ratio < 0.30:
                    capture_pct = round(capture_ratio * 100.0, 2)
                    bot_name = str(e.get("bot_name", "STE")).upper()
                    pair = str(e.get("pair", "BTC/INR")).upper()
                    insights.append(
                        {
                            "id": str(uuid.uuid4()),
                            "bot_name": bot_name,
                            "pair": pair,
                            "pattern_type": "LOW_MFE_EFFICIENCY",
                            "severity": "LOW" if capture_pct >= 20.0 else "MEDIUM",
                            "lesson_summary": f"Low MFE capture efficiency ({capture_pct}%) on winning trade for {pair}.",
                            "recommended_adjustment": f"Optimize trailing stop offset and delay premature take-profit triggers for strategy {bot_name}.",
                            "created_at": now_str,
                        }
                    )

        return insights

    def _detect_regime_mismatch(
        self, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Detect strategy underperformance during specific market regimes from tags."""
        insights: list[dict[str, Any]] = []
        now_str = datetime.now(timezone.utc).isoformat()

        regime_losses: dict[str, dict[str, int]] = {}

        for e in entries:
            tags = e.get("tags") or []
            bot_name = str(e.get("bot_name", "STE")).upper()
            net_pnl = float(e.get("net_pnl", 0.0))

            regime = (
                "BEARISH"
                if any(
                    "bear" in str(t).lower() or "risk_off" in str(t).lower()
                    for t in tags
                )
                else None
            )
            if regime and net_pnl < 0:
                regime_losses.setdefault(bot_name, {}).setdefault(regime, 0)
                regime_losses[bot_name][regime] += 1

                if regime_losses[bot_name][regime] >= 2:
                    insights.append(
                        {
                            "id": str(uuid.uuid4()),
                            "bot_name": bot_name,
                            "pair": str(e.get("pair", "BTC/INR")).upper(),
                            "pattern_type": "REGIME_MISMATCH",
                            "severity": "HIGH",
                            "lesson_summary": f"Strategy {bot_name} exhibits underperformance during {regime} market regime.",
                            "recommended_adjustment": f"Require higher confluence score threshold (90+) for {bot_name} during {regime} market conditions.",
                            "created_at": now_str,
                        }
                    )

        return insights
