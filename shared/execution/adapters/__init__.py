"""
Production Fleet Bot Execution Adapters Package.
"""

from __future__ import annotations

from core.types import BotName

from .base import BaseBotAdapter
from .bbs_adapter import BBSAdapter
from .hda_adapter import HDAAdapter
from .ste_adapter import STEAdapter
from .vcp_adapter import VCPAdapter


class StrategyAdapterFactory:
    """Factory for creating and looking up production strategy adapters."""

    _ADAPTERS: dict[BotName, BaseBotAdapter] = {
        BotName.STE: STEAdapter(),
        BotName.HDA: HDAAdapter(),
        BotName.VCP: VCPAdapter(),
        BotName.BBS: BBSAdapter(),
    }

    @classmethod
    def get_adapter(cls, bot_name: BotName | str) -> BaseBotAdapter:
        if isinstance(bot_name, str):
            bot_name_upper = bot_name.strip().upper()
            try:
                bot_name = BotName(bot_name_upper)
            except ValueError:
                raise ValueError(
                    f"InvalidStrategyError: Strategy '{bot_name}' is deprecated or unrecognized."
                )

        if bot_name not in cls._ADAPTERS:
            raise ValueError(
                f"InvalidStrategyError: No registered adapter for '{bot_name.value}'."
            )
        return cls._ADAPTERS[bot_name]

    @classmethod
    def get_all_adapters(cls) -> dict[BotName, BaseBotAdapter]:
        return dict(cls._ADAPTERS)


__all__ = [
    "BBSAdapter",
    "BaseBotAdapter",
    "HDAAdapter",
    "STEAdapter",
    "StrategyAdapterFactory",
    "VCPAdapter",
]
