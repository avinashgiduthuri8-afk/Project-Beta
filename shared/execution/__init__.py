"""Project Beta execution package with side-effect-free lazy exports."""

from importlib import import_module

_EXPORTS = {
    "AutoTradeRouter": ("execution.auto_trader", "AutoTradeRouter"),
    "PositionManager": ("execution.position_manager", "PositionManager"),
    "ReconciliationService": ("execution.reconciliation", "ReconciliationService"),
    "RestartRecoveryService": ("execution.recovery", "RestartRecoveryService"),
    "TradingService": ("execution.service", "TradingService"),
    "BaseBotAdapter": ("execution.adapters", "BaseBotAdapter"),
    "BBSAdapter": ("execution.adapters", "BBSAdapter"),
    "HDAAdapter": ("execution.adapters", "HDAAdapter"),
    "STEAdapter": ("execution.adapters", "STEAdapter"),
    "StrategyAdapterFactory": ("execution.adapters", "StrategyAdapterFactory"),
    "VCPAdapter": ("execution.adapters", "VCPAdapter"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
