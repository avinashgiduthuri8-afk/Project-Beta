"""Bot Registry: Central factory for initializing and accessing the archetype fleet."""

from typing import Dict, List, Type
from v2.core.types import BotName
from v2.core.bots.base import BotArchetype
from v2.core.bots.ste_bot import STEBot
from v2.core.bots.hda_bot import HDABot
from v2.core.bots.vcp_bot import VCPBot
from v2.core.bots.bbs_bot import BBSBot

class BotRegistry:
    """Manages the lifecycle and configuration of all quantitative bot archetypes."""

    def __init__(self):
        self._bots: Dict[BotName, BotArchetype] = {}
        self._register_default_fleet()

    def _register_default_fleet(self) -> None:
        """Initializes the production fleet with default calibrations."""
        self.register(STEBot())
        self.register(HDABot())
        self.register(VCPBot())
        self.register(BBSBot())

    def register(self, bot: BotArchetype) -> None:
        """Registers a bot instance."""
        self._bots[bot.name] = bot

    def get_bot(self, name: BotName) -> BotArchetype:
        """Retrieves a specific bot by name."""
        if name not in self._bots:
            raise KeyError(f"Bot {name.value} not found in registry.")
        return self._bots[name]

    def get_all_bots(self) -> List[BotArchetype]:
        """Returns all registered bots."""
        return list(self._bots.values())

