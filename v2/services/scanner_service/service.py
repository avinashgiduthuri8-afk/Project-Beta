"""
V2 ScannerService.

Bridges the V1 scanner HTTP API and the V2 event bus.

Responsibilities:
  - Poll GET /api/v1/scanner/signals on the scheduler interval
  - Transform V1 response → V2 Signal domain objects via adapter
  - Deduplicate: only publish SIGNAL_GENERATED for new signals
  - Detect expiry: publish SIGNAL_EXPIRED when a live signal passes TTL
  - Persist all signals to SignalRepository
  - Expose get_live_signals() for the API layer
  - Report health status

No V1 imports — coupling is via HTTP only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

from v2.bus.event_bus import EventBus
from v2.bus.event_types import EventType
from v2.core.config import V2Config
from v2.core.types import Priority, Signal
from v2.core.logging import get_logger
from v2.repository.signal_repo import SignalRepository
from v2.repository.event_log_repo import EventLogRepository

from .adapter import v1_response_to_signals
from .signal_filter import (
    filter_by_priority, filter_live, deduplicate, detect_expired,
)

logger = get_logger("v2.services.scanner_service")


class ScannerService:
    """
    Bridges V1 scanner → V2 event bus.

    Lifecycle:
        service = ScannerService(bus, signal_repo, event_log_repo, config)
        await service.start()         # subscribe handlers, called once at startup
        await service.poll()          # called by scheduler every N seconds
        await service.stop()          # unsubscribe, flush state
    """

    def __init__(
        self,
        bus: EventBus,
        signal_repo: SignalRepository,
        event_log_repo: EventLogRepository,
        config: V2Config,
    ) -> None:
        self._bus = bus
        self._signal_repo = signal_repo
        self._event_log = event_log_repo
        self._config = config

        # In-memory live signal cache  {signal_id: Signal}
        self._live: dict[str, Signal] = {}
        # Dedup set — {coin::generated_at} for signals already seen this session
        self._seen_keys: set[str] = set()

        self._poll_count = 0
        self._last_poll_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._started = False

        self._min_priority = Priority(self._config.v2_scanner_min_priority)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe bus handlers. Called once at application startup."""
        if self._started:
            return
        self._started = True
        await self._bus.publish(
            EventType.SYSTEM_STARTUP,
            {"service": "scanner_service"},
        )
        logger.info("ScannerService started")

    async def stop(self) -> None:
        """Unsubscribe and flush in-memory state."""
        self._started = False
        self._live.clear()
        logger.info("ScannerService stopped")

    # ── Polling (called by scheduler) ─────────────────────────────────────────

    async def poll(self) -> dict:
        """
        Fetch fresh signals from V1 scanner, update live cache, and publish events.

        Returns a summary dict for scheduler logging.
        """
        summary = {
            "fetched": 0,
            "new_signals": 0,
            "expired": 0,
            "errors": 0,
        }

        try:
            raw = await self._fetch_v1_signals()
            summary["fetched"] = len(raw)

            # 1. Adapt V1 → V2 Signal
            candidates = v1_response_to_signals(
                raw,
                signal_ttl_seconds=self._config.v2_scanner_signal_ttl,
            )

            # 2. Filter by minimum priority
            candidates = filter_by_priority(candidates, self._min_priority)

            # 3. Deduplicate against seen set
            new_signals, new_keys = deduplicate(candidates, self._seen_keys)
            self._seen_keys.update(new_keys)

            # 4. Persist new signals and publish events
            for sig in new_signals:
                await self._signal_repo.insert(sig)
                self._live[sig.id] = sig
                await self._publish_signal_generated(sig)
                summary["new_signals"] += 1

            # 5. Detect expiry in live cache
            live_list = list(self._live.values())
            still_live, newly_expired = detect_expired(live_list)

            for sig in newly_expired:
                del self._live[sig.id]
                await self._signal_repo.mark_expired(sig.id, reason="TTL")
                await self._publish_signal_expired(sig)
                summary["expired"] += 1

            self._poll_count += 1
            self._last_poll_at = datetime.now(timezone.utc)
            self._last_error = None
            logger.info(
                "Scanner poll complete",
                extra={**summary, "live_count": len(self._live)},
            )

        except Exception as exc:
            self._last_error = str(exc)
            summary["errors"] = 1
            logger.exception("Scanner poll failed", extra={"error": str(exc)})

        return summary

    async def check_expiry(self) -> int:
        """
        Check in-memory live signals for expiry (called by signal_expiry_check job).
        Returns count of signals expired.
        """
        live_list = list(self._live.values())
        _, newly_expired = detect_expired(live_list)
        for sig in newly_expired:
            del self._live[sig.id]
            await self._signal_repo.mark_expired(sig.id, reason="TTL")
            await self._publish_signal_expired(sig)
        return len(newly_expired)

    # ── Public query interface ─────────────────────────────────────────────────

    def get_live_signals(self) -> list[Signal]:
        """Return current live signals sorted by score desc."""
        return sorted(self._live.values(), key=lambda s: s.score, reverse=True)

    def get_health(self) -> dict:
        return {
            "poll_count":    self._poll_count,
            "last_poll_at":  self._last_poll_at.isoformat() if self._last_poll_at else None,
            "live_signals":  len(self._live),
            "last_error":    self._last_error,
            "healthy":       self._last_error is None and self._poll_count > 0,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_v1_signals(self) -> list[dict]:
        """Call V1 scanner signals endpoint and return raw list."""
        url = f"{self._config.v2_scanner_base_url}/signals"
        headers = {}
        if self._config.dashboard_api_key:
            headers["X-API-Key"] = self._config.dashboard_api_key

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            # Some V1 responses wrap in {"signals": [...]}
            if isinstance(data, dict):
                return data.get("signals", [])
            return []

    async def _publish_signal_generated(self, sig: Signal) -> None:
        payload = {
            "signal_id":    sig.id,
            "coin":         sig.coin,
            "pair":         sig.pair,
            "priority":     sig.priority.value,
            "score":        sig.score,
            "market_state": sig.market_state.value,
            "expires_at":   sig.expires_at.isoformat(),
            "source":       "scanner_service",
        }
        await self._bus.publish(EventType.SIGNAL_GENERATED, payload)
        await self._event_log.append(
            event_type     = EventType.SIGNAL_GENERATED.value,
            payload        = payload,
            source_service = "scanner_service",
            entity_id      = sig.id,
        )

    async def _publish_signal_expired(self, sig: Signal) -> None:
        payload = {
            "signal_id": sig.id,
            "coin":      sig.coin,
            "reason":    "TTL",
        }
        await self._bus.publish(EventType.SIGNAL_EXPIRED, payload)
        await self._event_log.append(
            event_type     = EventType.SIGNAL_EXPIRED.value,
            payload        = payload,
            source_service = "scanner_service",
            entity_id      = sig.id,
        )
