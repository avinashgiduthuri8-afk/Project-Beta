"""Production Fleet: 4 Quantitative Bot Archetypes."""

from v2.core.bots.base import BotArchetype
from v2.core.bots.registry import BotRegistry
from v2.core.bots.ste_bot import STEBot
from v2.core.bots.hda_bot import HDABot
from v2.core.bots.vcp_bot import VCPBot
from v2.core.bots.bbs_bot import BBSBot

__all__ = [
    "BotArchetype",
    "BotRegistry",
    "STEBot",
    "HDABot",
    "VCPBot",
    "BBSBot",
]

