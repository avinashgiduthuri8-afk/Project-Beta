"""Base class for all quantitative bot archetypes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import pandas as pd

from v2.core.types import BotName

class BotArchetype(ABC):
    """
    Abstract base class enforcing a common evaluation interface 
    across all quantitative trading archetypes.
    """

    @property
    @abstractmethod
    def name(self) -> BotName:
        """The canonical BotName enum for this archetype."""
        pass

    @abstractmethod
    def evaluate_setup(
        self, 
        symbol: str, 
        df: pd.DataFrame, 
        **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates a potential trade setup based on historical price data.
        Returns setup parameters dict if conditions are met, otherwise None.
        """
        pass

