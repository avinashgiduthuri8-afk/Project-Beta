"""Compatibility bridge from Alpha's router contract to Indian brokers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.types import BotName
from execution.brokers import BrokerOrder, build_broker


@dataclass
class IndianExecutionConfig:
    default_trade_amount_inr: float = 10_000.0
    product: str = "CNC"
    exchange: str = "NSE"


class IndianBrokerExecutionClient:
    def __init__(self, app_config: Any) -> None:
        self.app_config = app_config
        self.config = IndianExecutionConfig(
            default_trade_amount_inr=float(
                getattr(app_config, "order_size_inr", 10_000.0) or 10_000.0
            ),
            product=str(getattr(app_config, "product_type", "CNC") or "CNC"),
            exchange=str(getattr(app_config, "exchange", "NSE") or "NSE"),
        )
        self.broker = build_broker(app_config)
        self.subaccount_id = str(getattr(app_config, "broker", "PAPER") or "PAPER").upper()

    @staticmethod
    def _split_symbol(pair: str, default_exchange: str) -> tuple[str, str]:
        clean = pair.upper().strip().replace("/INR", "")
        if ":" in clean:
            exchange, symbol = clean.split(":", 1)
            return exchange, symbol
        return default_exchange.upper(), clean

    async def place_order(
        self,
        pair: str,
        side: str,
        price: float | None = None,
        qty: float | None = None,
        total_quantity: float | None = None,
        order_type: str = "MARKET",
        stop_price: float | None = None,
        client_order_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        exchange, symbol = self._split_symbol(pair, self.config.exchange)
        quantity = int(total_quantity if total_quantity is not None else (qty or 0))
        mapped_type = order_type.upper().replace("STOP_LIMIT", "SL")
        order = BrokerOrder(
            symbol=symbol,
            exchange=exchange,
            side=side.upper(),
            quantity=quantity,
            product=self.config.product,
            order_type=mapped_type,
            price=price,
            trigger_price=stop_price,
            client_order_id=client_order_id,
        )
        result = await self.broker.place_order(order)
        if result.get("success"):
            result.setdefault("exchange_order_id", result.get("order_id"))
        return result

    async def place_live_order(self, **kwargs: Any) -> dict[str, Any]:
        return await self.place_order(**kwargs)

    async def cancel_order(self, order_id: str, **_: Any) -> dict[str, Any]:
        return await self.broker.cancel_order(order_id)

    async def close(self) -> None:
        await self.broker.close()


class IndianBrokerManager:
    """One broker account with strategy labels, matching the prior manager API."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self._client = IndianBrokerExecutionClient(config)

    def get_client(self, bot_name: BotName = BotName.STE) -> IndianBrokerExecutionClient:
        return self._client

    @property
    def clients(self) -> dict[BotName, IndianBrokerExecutionClient]:
        return {bot: self._client for bot in BotName}

    async def close(self) -> None:
        await self._client.close()
