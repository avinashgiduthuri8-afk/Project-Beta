"""
PROJECT-ALPHA Exception Hierarchy.

All PROJECT-ALPHA exceptions derive from AlphaError.
"""

from __future__ import annotations


class AlphaError(Exception):
    """Base for all PROJECT-ALPHA exceptions."""


# Canonical alias for backward compatibility
V2Error = AlphaError


# ── Configuration ─────────────────────────────────────────────────────────────


class ConfigError(AlphaError):
    """Missing or invalid configuration value."""


class SecurityConfigError(ConfigError):
    """Missing or insecure production live security configuration."""


# ── Storage / persistence ─────────────────────────────────────────────────────


class StorageError(AlphaError):
    """Database or repository operation failed."""


class MigrationError(StorageError):
    """Schema migration failed."""


# ── Business logic ────────────────────────────────────────────────────────────


class ServiceError(AlphaError):
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


class SchedulerError(AlphaError):
    """Job registration or execution failed."""


class BusError(AlphaError):
    """Event publish or subscribe failed."""
