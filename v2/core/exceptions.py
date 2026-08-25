"""
V2 Exception Hierarchy.

All V2 exceptions derive from V2Error so callers can catch the whole
family with a single except clause when needed.
"""

from __future__ import annotations


class V2Error(Exception):
    """Base for all V2 exceptions."""


# ── Configuration ─────────────────────────────────────────────────────────────

class ConfigError(V2Error):
    """Missing or invalid configuration value."""


# ── Storage / persistence ─────────────────────────────────────────────────────

class StorageError(V2Error):
    """Database or repository operation failed."""


class MigrationError(StorageError):
    """Schema migration failed."""


# ── Business logic ────────────────────────────────────────────────────────────

class ServiceError(V2Error):
    """Generic business-logic failure."""


class RiskDenied(ServiceError):
    """Trade blocked by RiskService; payload includes code and reason."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"[{code}] {reason}")


class SignalExpired(ServiceError):
    """Signal has passed its TTL and cannot be acted upon."""


# ── Infrastructure ────────────────────────────────────────────────────────────

class SchedulerError(V2Error):
    """Job registration or execution failed."""


class BusError(V2Error):
    """Event publish or subscribe failed."""
