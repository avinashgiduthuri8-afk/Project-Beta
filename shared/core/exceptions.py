"""Project Beta exception hierarchy."""


class BetaError(Exception):
    """Base exception for Project Beta."""


class ConfigError(BetaError):
    pass


class StorageError(BetaError):
    pass


class BusError(BetaError):
    pass
