"""
V2 CalibrationWorker — Dynamic Win-Rate Feedback Calibration Engine.

Periodically inspects rolling performance datasets (tier accuracy, coin performance,
signal history) and automatically calibrates:
  - Strict Confluence Threshold (tightens to 90 if rolling 7d win rate < 50%)
  - Per-Coin Risk Penalties (penalizes or flags coins with 7d win rate < 40% over >=5 signals)
  - Emits CALIBRATION_UPDATED events to the EventBus.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.db import Database
from core.repository.feedback_repo import FeedbackRepository

from .confluence_engine import ConfluenceEngine

logger = get_logger("scanner.calibration_worker")


def get_data_file_path(filename: str) -> Path:
    """Resolve data file location checking v2/data/ and data/."""
    root = Path(__file__).resolve().parents[3]
    candidates = [
        root / "v2" / "data" / filename,
        root / "data" / filename,
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _safe_load_json(file_path: Path) -> Any:
    """Non-blocking thread-safe JSON file reader."""
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.debug("Failed to read JSON from %s: %s", file_path, exc)
        return None


class CalibrationWorker:
    """
    Dynamic feedback worker running periodically to audit performance
    and auto-calibrate scanner parameters.
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        confluence_engine: ConfluenceEngine | None = None,
        interval_seconds: int = 900,  # 15 minutes
        base_threshold: int = 85,
        tightened_threshold: int = 90,
        feedback_repo: FeedbackRepository | None = None,
        feedback_db_path: str | Path | None = None,
    ) -> None:
        self._bus = bus
        self._confluence_engine = confluence_engine
        self._interval = interval_seconds
        self._base_threshold = base_threshold
        self._tightened_threshold = tightened_threshold
        self._feedback_repo = feedback_repo
        self._feedback_db_path = Path(feedback_db_path) if feedback_db_path else None
        self._feedback_db: Database | None = None

        self._running = False
        self._task: asyncio.Task | None = None
        self._last_run_at: datetime | None = None

        # Current calibration state
        self.current_strict_threshold: int = base_threshold
        self.current_coin_penalties: dict[str, int] = {}
        self.underperforming_coins: list[str] = []
        self.rolling_fleet_win_rate: float = 0.0
        self.tightening_active: bool = False
        self.active_strategy_calibrations: list[dict[str, Any]] = []

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the background calibration loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "CalibrationWorker background task started (interval: %ds)", self._interval
        )

    async def stop(self) -> None:
        """Stop the background calibration loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._feedback_db:
            await self._feedback_db.close()
            self._feedback_db = None
            self._feedback_repo = None
        logger.info("CalibrationWorker stopped")

    async def _connect_feedback_repo(self) -> None:
        if self._feedback_repo or not self._feedback_db_path:
            return
        if not self._feedback_db_path.exists():
            return
        try:
            self._feedback_db = Database(
                str(self._feedback_db_path), read_only=True
            )
            await self._feedback_db.open()
            self._feedback_repo = FeedbackRepository(
                self._feedback_db.connection
            )
            logger.info(
                "CalibrationWorker connected to learner DB (read-only): %s",
                self._feedback_db_path,
            )
        except Exception as exc:
            logger.warning("Could not connect to learner calibration DB: %s", exc)
            if self._feedback_db:
                await self._feedback_db.close()
            self._feedback_db = None
            self._feedback_repo = None

    async def _loop(self) -> None:
        # Run an initial cycle on start
        await self.run_calibration_cycle()
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                await self.run_calibration_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error in CalibrationWorker loop: %s", exc)

    async def run_calibration_cycle(self) -> dict[str, Any]:
        """
        Execute a full calibration analysis cycle:
          1. Reads tier_accuracy.json and coin_performance.json in thread executor.
          2. Calculates fleet-wide win rate and coin health.
          3. Updates threshold and coin penalties.
          4. Applies changes to ConfluenceEngine.
          5. Emits CALIBRATION_UPDATED event.
        """
        tier_file = get_data_file_path("tier_accuracy.json")
        coin_file = get_data_file_path("coin_performance.json")
        history_file = get_data_file_path("signal_history.json")

        tier_data = await asyncio.to_thread(_safe_load_json, tier_file) or {}
        coin_data = await asyncio.to_thread(_safe_load_json, coin_file) or {}
        history_data = await asyncio.to_thread(_safe_load_json, history_file) or []

        # 1. Evaluate Overall Fleet Rolling Win Rate
        total_signals = 0
        total_wins = 0

        # Check signal history for last 7 days first if available
        if isinstance(history_data, list) and history_data:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            for item in history_data:
                ts_str = item.get("timestamp") or item.get("generated_at")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts >= cutoff:
                            total_signals += 1
                            if (
                                item.get("outcome") == "win"
                                or float(item.get("return_pct", 0.0) or 0.0) > 0
                            ):
                                total_wins += 1
                    except Exception:
                        continue

        # Fallback to tier_accuracy.json aggregate if history is small
        if total_signals < 5 and isinstance(tier_data, dict) and tier_data:
            for t_info in tier_data.values():
                if isinstance(t_info, dict):
                    total_signals += int(t_info.get("total_signals", 0))
                    total_wins += int(t_info.get("winning_signals", 0))

        if total_signals > 0:
            fleet_win_rate = round((total_wins / total_signals) * 100.0, 1)
        else:
            fleet_win_rate = 65.0  # Baseline assumption if cold start

        self.rolling_fleet_win_rate = fleet_win_rate

        # 2. Compute Strict Threshold Adjustment
        if total_signals >= 5 and fleet_win_rate < 50.0:
            self.current_strict_threshold = (
                self._tightened_threshold
            )  # Tightening mode: 90
            self.tightening_active = True
        elif fleet_win_rate >= 75.0 or total_signals < 5:
            self.current_strict_threshold = self._base_threshold  # Base mode: 85
            self.tightening_active = False

        # 3. Evaluate Per-Coin Penalties
        coin_penalties: dict[str, int] = {}
        underperforming: list[str] = []

        if isinstance(coin_data, dict):
            for coin, stats in coin_data.items():
                if isinstance(stats, dict):
                    c_total = int(stats.get("total_signals", 0))
                    c_win_rate = float(stats.get("win_rate_pct", 0.0))
                    if c_total >= 5 and c_win_rate < 40.0:
                        coin_penalties[coin.upper()] = -20
                        underperforming.append(coin.upper())

        self.current_coin_penalties = coin_penalties
        self.underperforming_coins = underperforming
        self._last_run_at = datetime.now(timezone.utc)

        # Learner DB is the authoritative source for promoted strategy settings.
        await self._connect_feedback_repo()
        if self._feedback_repo:
            try:
                self.active_strategy_calibrations = (
                    await self._feedback_repo.get_all_active_calibrations()
                )
            except Exception as exc:
                logger.warning("Could not refresh learner calibrations: %s", exc)

        # 4. Apply to Confluence Engine
        if self._confluence_engine:
            self.apply_calibration(self._confluence_engine)

        # 5. Emit CALIBRATION_UPDATED event
        payload = {
            "strict_threshold": self.current_strict_threshold,
            "tightening_active": self.tightening_active,
            "fleet_win_rate": self.rolling_fleet_win_rate,
            "coin_penalties": self.current_coin_penalties,
            "underperforming_coins": self.underperforming_coins,
            "strategy_calibrations": self.active_strategy_calibrations,
            "timestamp": self._last_run_at.isoformat(),
        }

        if self._bus:
            await self._bus.publish(EventType.CALIBRATION_UPDATED, payload)

        logger.info(
            "Calibration cycle complete: Strict Threshold=%d (Tightening=%s), Fleet Win Rate=%.1f%%, Penalized Coins=%s",
            self.current_strict_threshold,
            self.tightening_active,
            self.rolling_fleet_win_rate,
            self.underperforming_coins,
        )

        return payload

    def apply_calibration(self, confluence_engine: ConfluenceEngine) -> None:
        """Dynamically update threshold and coin penalties on the confluence engine."""
        confluence_engine.strict_threshold = self.current_strict_threshold
        confluence_engine.coin_penalties = dict(self.current_coin_penalties)
        confluence_engine.set_strategy_calibrations(
            self.active_strategy_calibrations
        )
