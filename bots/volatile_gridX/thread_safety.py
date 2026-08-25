"""
PROJECT-ALPHA Thread Safety Module
Provides mutex locks and atomic operations for production-grade trading.

BUG-001 FIX: Race condition protection for concurrent order execution.
"""

import threading
import functools
import logging
from contextlib import contextmanager
from typing import Callable, Any

logger = logging.getLogger("vgx.thread_safety")

# ============================================================
# GLOBAL LOCKS
# ============================================================

# Position management lock - prevents duplicate orders
_position_lock = threading.RLock()

# Storage I/O lock - prevents concurrent file writes
_storage_lock = threading.RLock()

# Balance update lock - prevents balance corruption
_balance_lock = threading.RLock()

# Order execution lock - ensures atomic order processing
_order_lock = threading.RLock()

# Analytics lock - protects trade history updates
_analytics_lock = threading.RLock()


# ============================================================
# LOCK CONTEXT MANAGERS
# ============================================================

@contextmanager
def position_lock():
    """
    Thread-safe context manager for position operations.
    Use for: create, update, close position operations.
    """
    acquired = _position_lock.acquire(timeout=10.0)
    if not acquired:
        logger.error("POSITION_LOCK: Timeout acquiring lock - potential deadlock")
        raise RuntimeError("Position lock acquisition timeout")
    try:
        yield
    finally:
        _position_lock.release()


@contextmanager
def storage_lock():
    """
    Thread-safe context manager for storage I/O.
    Use for: JSON file read/write operations.
    """
    acquired = _storage_lock.acquire(timeout=10.0)
    if not acquired:
        logger.error("STORAGE_LOCK: Timeout acquiring lock")
        raise RuntimeError("Storage lock acquisition timeout")
    try:
        yield
    finally:
        _storage_lock.release()


@contextmanager
def balance_lock():
    """
    Thread-safe context manager for balance updates.
    Use for: virtual_balance modifications.
    """
    acquired = _balance_lock.acquire(timeout=5.0)
    if not acquired:
        logger.error("BALANCE_LOCK: Timeout acquiring lock")
        raise RuntimeError("Balance lock acquisition timeout")
    try:
        yield
    finally:
        _balance_lock.release()


@contextmanager
def order_lock():
    """
    Thread-safe context manager for order execution.
    Highest priority lock - use for atomic buy/sell operations.
    """
    acquired = _order_lock.acquire(timeout=15.0)
    if not acquired:
        logger.error("ORDER_LOCK: Timeout acquiring lock - critical failure")
        raise RuntimeError("Order lock acquisition timeout")
    try:
        yield
    finally:
        _order_lock.release()


@contextmanager
def analytics_lock():
    """
    Thread-safe context manager for analytics/history updates.
    """
    acquired = _analytics_lock.acquire(timeout=5.0)
    if not acquired:
        logger.warning("ANALYTICS_LOCK: Timeout - non-critical")
        raise RuntimeError("Analytics lock acquisition timeout")
    try:
        yield
    finally:
        _analytics_lock.release()


# ============================================================
# DECORATORS
# ============================================================

def thread_safe_position(func: Callable) -> Callable:
    """Decorator to make position operations thread-safe."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        with position_lock():
            return func(*args, **kwargs)
    return wrapper


def thread_safe_storage(func: Callable) -> Callable:
    """Decorator to make storage operations thread-safe."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        with storage_lock():
            return func(*args, **kwargs)
    return wrapper


def thread_safe_order(func: Callable) -> Callable:
    """Decorator for atomic order execution."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        with order_lock():
            return func(*args, **kwargs)
    return wrapper


# ============================================================
# DUPLICATE ORDER PREVENTION
# ============================================================

_pending_orders: set = set()
_pending_orders_lock = threading.Lock()


def mark_order_pending(symbol: str) -> bool:
    """
    Mark an order as pending to prevent duplicates.
    Returns True if order can proceed, False if duplicate detected.
    """
    with _pending_orders_lock:
        if symbol in _pending_orders:
            logger.warning("DUPLICATE_ORDER_BLOCKED: %s already pending", symbol)
            return False
        _pending_orders.add(symbol)
        return True


def clear_order_pending(symbol: str) -> None:
    """Clear pending status after order completes."""
    with _pending_orders_lock:
        _pending_orders.discard(symbol)


@contextmanager
def order_guard(symbol: str):
    """
    Context manager that prevents duplicate orders for the same symbol.
    
    Usage:
        with order_guard("BTC_SCANNER"):
            # Execute order - guaranteed no duplicate
    """
    if not mark_order_pending(symbol):
        raise ValueError(f"Duplicate order blocked for {symbol}")
    try:
        yield
    finally:
        clear_order_pending(symbol)


# ============================================================
# LOCK STATUS MONITORING
# ============================================================

def get_lock_status() -> dict:
    """Return current lock status for monitoring."""
    return {
        "position_lock_locked": _position_lock.locked() if hasattr(_position_lock, 'locked') else "N/A",
        "storage_lock_locked": _storage_lock.locked() if hasattr(_storage_lock, 'locked') else "N/A",
        "balance_lock_locked": _balance_lock.locked() if hasattr(_balance_lock, 'locked') else "N/A",
        "order_lock_locked": _order_lock.locked() if hasattr(_order_lock, 'locked') else "N/A",
        "pending_orders": list(_pending_orders),
    }
