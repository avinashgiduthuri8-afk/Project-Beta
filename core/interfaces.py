"""Abstract Base Interfaces for Project-Beta subsystems."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from core.models import OrderRequest, Order, Position, AccountBalance, Tick, Candle


class BaseBroker(ABC):
    """Abstract interface for multi-broker adapters."""

    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the broker API (OAuth, TOTP, session tokens)."""
        pass

    @abstractmethod
    def get_profile(self) -> Dict[str, Any]:
        """Fetch broker user profile information."""
        pass

    @abstractmethod
    def get_funds(self) -> AccountBalance:
        """Fetch margins, available cash, and account funds."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Fetch current active and closed positions."""
        pass

    @abstractmethod
    def get_orders(self) -> List[Order]:
        """Fetch daily order book."""
        pass

    @abstractmethod
    def place_order(self, request: OrderRequest) -> Order:
        """Submit a new order to the exchange."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open/pending order."""
        pass

    @abstractmethod
    def modify_order(self, order_id: str, quantity: Optional[int] = None, price: Optional[float] = None, trigger_price: Optional[float] = None) -> Order:
        """Modify price/quantity/trigger of an open order."""
        pass


class BaseStrategy(ABC):
    """Abstract interface for algorithmic trading strategies."""

    @abstractmethod
    def on_tick(self, tick: Tick) -> None:
        """Handle incoming real-time market tick."""
        pass

    @abstractmethod
    def on_candle(self, candle: Candle) -> None:
        """Handle completed OHLCV candle event."""
        pass

    @abstractmethod
    def on_order_update(self, order: Order) -> None:
        """Handle broker order status transitions."""
        pass


class BaseRiskEngine(ABC):
    """Abstract interface for pre-trade and in-flight risk validation."""

    @abstractmethod
    def validate_order(self, request: OrderRequest, current_balance: AccountBalance, current_positions: List[Position]) -> tuple[bool, str]:
        """Validate order against RMS limits before routing."""
        pass


class BaseNotifier(ABC):
    """Abstract interface for notification dispatchers."""

    @abstractmethod
    def send_alert(self, title: str, message: str, level: str = "INFO") -> bool:
        """Send notification alert."""
        pass
