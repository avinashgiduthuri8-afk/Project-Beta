"""
V2 Shared Core — zero business logic, imported by all V2 modules.
"""

from .config import V2Config, get_config
from .exceptions import (
    V2Error, ConfigError, StorageError, MigrationError,
    ServiceError, RiskDenied, SignalExpired, SchedulerError, BusError,
)
from .types import (
    Signal, Position, Trade, BotSnapshot,
    BotName, BotMode, BotStatus, Priority, RiskLevel,
    MarketState, OppType, ExitReason, PositionStatus,
)

__all__ = [
    "V2Config", "get_config",
    "V2Error", "ConfigError", "StorageError", "MigrationError",
    "ServiceError", "RiskDenied", "SignalExpired", "SchedulerError", "BusError",
    "Signal", "Position", "Trade", "BotSnapshot",
    "BotName", "BotMode", "BotStatus", "Priority", "RiskLevel",
    "MarketState", "OppType", "ExitReason", "PositionStatus",
]
