"""
PROJECT-ALPHA — Order Placement Idempotency Engine (Skill 07).

Guarantees exactly-once order execution across concurrent ticks, asynchronous
signal arrivals, and network retries:
  1. Deterministic Client Order ID Generation: Consistent, collision-free IDs.
  2. Granular Deduplication Locks: Per-signal and per-coin concurrency serialization.
  3. In-Flight & Completion State Machine: Distinguishes between active and settled attempts.
  4. Result Caching & Duplicate Handling: Returns cached response on redundant attempts.
  5. Cache TTL & Eviction: Prevents unbounded memory growth.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Coroutine

from core.logging import get_logger

logger = get_logger("execution.trading.idempotency")


class IdempotencyState(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class IdempotencyRecord:
    key: str
    client_order_id: str
    state: IdempotencyState
    created_at: float
    updated_at: float
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 1


class IdempotencyGuard:
    """
    Idempotency Guard & Deduplication Engine for Order Placement.
    Serializes concurrent events and guarantees exactly-once dispatch.
    """

    def __init__(self, ttl_seconds: float = 3600.0, max_cache_size: int = 10000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_cache_size = max_cache_size
        self._records: dict[str, IdempotencyRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sync_lock = threading.RLock()

    def generate_idempotency_key(self, coin: str, signal_id: str) -> str:
        """Generate canonical idempotency key for coin and signal."""
        clean_coin = coin.upper().replace("/", "").replace("_", "")
        clean_sig = str(signal_id).strip()
        return f"{clean_coin}::{clean_sig}"

    def generate_client_order_id(
        self,
        coin: str,
        side: str,
        signal_id: str,
        nonce: str | None = None,
    ) -> str:
        """
        Generate deterministic, exchange-compliant client order ID.
        Format: ORD_{COIN}_{SIDE}_{SIG_SHORT}_{HASH}
        """
        clean_coin = coin.upper().replace("/", "").replace("_", "")
        clean_side = side.upper()
        clean_sig = str(signal_id).replace("-", "")[:8]
        # Generate a 6-character deterministic suffix hash
        seed = f"{clean_coin}:{clean_side}:{signal_id}:{nonce or ''}"
        hash_suffix = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]

        client_id = f"ORD_{clean_coin}_{clean_side}_{clean_sig}_{hash_suffix}"
        # Truncate to 36 chars if necessary for exchange constraints
        return client_id[:36]

    def _get_async_lock(self, key: str) -> asyncio.Lock:
        with self._sync_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _cleanup_expired(self) -> None:
        """Prune expired records beyond TTL or when cache exceeds maximum size."""
        now = time.time()
        with self._sync_lock:
            expired_keys = [
                k for k, r in self._records.items()
                if (now - r.updated_at) > self.ttl_seconds
            ]
            for k in expired_keys:
                del self._records[k]
                self._locks.pop(k, None)

            # If still exceeding capacity, remove oldest records
            if len(self._records) > self.max_cache_size:
                sorted_keys = sorted(self._records.keys(), key=lambda k: self._records[k].updated_at)
                overflow_count = len(self._records) - self.max_cache_size
                for k in sorted_keys[:overflow_count]:
                    del self._records[k]
                    self._locks.pop(k, None)

    def is_processed(self, key: str) -> bool:
        """Check if an idempotency key has already reached COMPLETED state."""
        with self._sync_lock:
            record = self._records.get(key)
            if not record:
                return False
            # Check TTL
            if (time.time() - record.updated_at) > self.ttl_seconds:
                return False
            return record.state == IdempotencyState.COMPLETED

    def is_in_flight(self, key: str) -> bool:
        """Check if an order for this key is currently in flight."""
        with self._sync_lock:
            record = self._records.get(key)
            if not record:
                return False
            return record.state == IdempotencyState.IN_FLIGHT

    def get_record(self, key: str) -> IdempotencyRecord | None:
        """Retrieve record for key if not expired."""
        with self._sync_lock:
            return self._records.get(key)

    def record_in_flight(self, key: str, client_order_id: str) -> IdempotencyRecord:
        """Mark order attempt as in flight."""
        now = time.time()
        with self._sync_lock:
            self._cleanup_expired()
            if key in self._records:
                record = self._records[key]
                record.state = IdempotencyState.IN_FLIGHT
                record.updated_at = now
                record.attempts += 1
            else:
                record = IdempotencyRecord(
                    key=key,
                    client_order_id=client_order_id,
                    state=IdempotencyState.IN_FLIGHT,
                    created_at=now,
                    updated_at=now,
                )
                self._records[key] = record
            return record

    def record_completed(self, key: str, result: dict[str, Any]) -> None:
        """Mark order attempt as successfully completed and cache result."""
        now = time.time()
        with self._sync_lock:
            if key in self._records:
                record = self._records[key]
                record.state = IdempotencyState.COMPLETED
                record.updated_at = now
                record.result = result
            else:
                self._records[key] = IdempotencyRecord(
                    key=key,
                    client_order_id=result.get("client_order_id", ""),
                    state=IdempotencyState.COMPLETED,
                    created_at=now,
                    updated_at=now,
                    result=result,
                )

    def record_failed(self, key: str, error: str) -> None:
        """Mark order attempt as failed."""
        now = time.time()
        with self._sync_lock:
            if key in self._records:
                record = self._records[key]
                record.state = IdempotencyState.FAILED
                record.updated_at = now
                record.error = error
            else:
                self._records[key] = IdempotencyRecord(
                    key=key,
                    client_order_id="",
                    state=IdempotencyState.FAILED,
                    created_at=now,
                    updated_at=now,
                    error=error,
                )

    async def execute_idempotent(
        self,
        coin: str,
        side: str,
        signal_id: str,
        dispatch_coro: Callable[[str], Coroutine[Any, Any, dict[str, Any]]],
    ) -> dict[str, Any]:
        """
        Execute order placement idempotently with deduplication locking.

        If order is already completed, returns cached result with duplicate indicator.
        If order is currently in-flight, returns in-flight rejection.
        Otherwise, executes dispatch_coro with the generated client_order_id.
        """
        key = self.generate_idempotency_key(coin, signal_id)
        lock = self._get_async_lock(key)

        async with lock:
            with self._sync_lock:
                record = self._records.get(key)
                if record:
                    if record.state == IdempotencyState.COMPLETED:
                        logger.info("Idempotent hit: returning cached result for %s", key)
                        cached = dict(record.result or {})
                        cached["is_duplicate"] = True
                        cached["idempotent_cached"] = True
                        cached["idempotency_key"] = key
                        return cached
                    elif record.state == IdempotencyState.IN_FLIGHT:
                        logger.warning("Concurrent duplicate in-flight rejected for %s", key)
                        return {
                            "success": False,
                            "error": "DUPLICATE_ORDER_IN_FLIGHT",
                            "message": f"Order placement for {key} is already in progress.",
                            "idempotency_key": key,
                            "client_order_id": record.client_order_id,
                        }

            client_order_id = self.generate_client_order_id(coin, side, signal_id)
            self.record_in_flight(key, client_order_id)

            try:
                result = await dispatch_coro(client_order_id)
                if result.get("success"):
                    self.record_completed(key, result)
                else:
                    self.record_failed(key, str(result.get("error") or "Order failed"))
                return result
            except Exception as exc:
                self.record_failed(key, str(exc))
                logger.error("Exception during idempotent order execution for %s: %s", key, exc)
                raise
