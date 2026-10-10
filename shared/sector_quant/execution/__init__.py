"""
sector_quant.execution — Execution simulation and broker interfaces.
"""

from .simulated import ExecutionHandler, SimulatedExecutionHandler

__all__ = [
    "ExecutionHandler",
    "SimulatedExecutionHandler",
]
