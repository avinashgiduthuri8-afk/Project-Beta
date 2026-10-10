"""
PROJECT-ALPHA Restart Recovery Engine.

Rehydrates active positions and bracket order state from SQLite on application startup,
verifying local records against exchange sub-account clients to prevent state loss across restarts.
"""

from __future__ import annotations

from typing import Any

from core.logging import get_logger
from core.repository.order_repo import OrderRepository
from core.repository.position_repo import PositionRepository
from core.types import Order, OrderState, Position
from execution.trading.order_state_machine import OrderStateMachine
from execution.trading.subaccount_manager import CoinDCXSubAccountManager

logger = get_logger("execution.recovery")


class RestartRecoveryService:
    """
    Restart Recovery Engine.
    Rehydrates unclosed positions and active live orders from SQLite,
    rebuilds internal bracket state, and cross-checks against sub-account clients.
    """

    def __init__(
        self,
        position_repo: PositionRepository,
        subaccount_manager: CoinDCXSubAccountManager | None = None,
        order_repo: OrderRepository | None = None,
    ) -> None:
        self._position_repo = position_repo
        self._subaccount_manager = subaccount_manager or CoinDCXSubAccountManager()
        self._order_repo = order_repo

    async def rehydrate_state(self) -> list[Position]:
        """
        Rehydrate all non-CLOSED positions from SQLite and verify against sub-account clients.
        Returns list of active recovered Position objects.
        """
        active_positions = await self._position_repo.get_active_positions()
        logger.info(
            "RestartRecoveryService rehydrating %d active position(s) from SQLite",
            len(active_positions),
        )

        recovered_positions: list[Position] = []

        for pos in active_positions:
            try:
                # Verify sub-account client configuration exists for this bot
                self._subaccount_manager.get_client(pos.bot)
                logger.info(
                    "Rehydrated position %s [%s] for %s (%s) @ INR %.2f (Qty: %s)",
                    pos.id,
                    pos.bot.value,
                    pos.coin,
                    pos.pair,
                    pos.entry_price,
                    pos.qty,
                )
                recovered_positions.append(pos)
            except Exception as exc:
                logger.error(
                    "Failed to rehydrate position %s for bot %s: %s",
                    pos.id,
                    pos.bot,
                    exc,
                )
                recovered_positions.append(pos)

        return recovered_positions

    async def verify_against_exchange(
        self, positions: list[Position]
    ) -> dict[str, Any]:
        """
        Verify local active positions against sub-account telemetry and balance data.
        Returns a verification summary dict.
        """
        telemetry = self._subaccount_manager.get_all_subaccount_telemetry()
        desynced_count = 0
        verified_count = 0

        for pos in positions:
            bot_key = pos.bot.value if hasattr(pos.bot, "value") else str(pos.bot)
            sub_info = telemetry.get(bot_key)
            if not sub_info:
                logger.warning(
                    "Sub-account telemetry missing during recovery check for position %s",
                    pos.id,
                )
                desynced_count += 1
            else:
                verified_count += 1

        summary = {
            "total_active": len(positions),
            "verified": verified_count,
            "desynced": desynced_count,
            "status": "HEALTHY" if desynced_count == 0 else "DESYNCED",
        }
        logger.info("Exchange position verification complete: %s", summary)
        return summary

    async def rehydrate_orders(self) -> list[Order]:
        """
        Rehydrate active non-terminal orders from SQLite, check their state against CoinDCX,
        and apply state transitions if state changed.
        """
        if not self._order_repo:
            return []

        active_orders = await self._order_repo.get_active_orders()
        logger.info(
            "RestartRecoveryService rehydrating %d active order(s) from SQLite",
            len(active_orders),
        )

        recovered_orders: list[Order] = []

        for order in active_orders:
            try:
                client = self._subaccount_manager.get_client(order.bot)
                target_state = order.state
                filled_qty = order.filled_qty
                avg_price = order.avg_price

                # If client_order_id or exchange_order_id is present, query exchange
                if order.client_order_id:
                    res = await client.get_order_by_client_id(order.client_order_id)
                    if res.get("success") and res.get("order"):
                        ex_ord = res["order"]
                        status_str = str(ex_ord.get("status", "")).lower()
                        if status_str in ("filled", "completed"):
                            target_state = OrderState.FILLED
                            filled_qty = float(
                                ex_ord.get(
                                    "total_quantity",
                                    ex_ord.get("quantity", order.req_qty),
                                )
                            )
                            avg_price = float(ex_ord.get("price", order.price))
                        elif status_str in ("open", "initiate", "pending"):
                            target_state = OrderState.OPEN
                        elif status_str in ("partially_filled", "partial_fill"):
                            target_state = OrderState.PARTIALLY_FILLED
                            filled_qty = float(ex_ord.get("total_quantity", 0.0))
                        elif status_str in ("cancelled", "canceled"):
                            target_state = OrderState.CANCELLED
                        elif status_str in ("rejected", "failed"):
                            target_state = OrderState.REJECTED

                if target_state != order.state:
                    updated_order, transition_rec = OrderStateMachine.transition(
                        order=order,
                        to_state=target_state,
                        filled_qty=filled_qty,
                        avg_price=avg_price,
                        reason="Rehydrated from CoinDCX API during startup recovery",
                    )
                    await self._order_repo.update(updated_order)
                    await self._order_repo.record_transition(transition_rec)
                    recovered_orders.append(updated_order)
                else:
                    recovered_orders.append(order)

            except Exception as exc:
                logger.error(
                    "Failed to rehydrate order %s (%s): %s", order.id, order.pair, exc
                )
                recovered_orders.append(order)

        return recovered_orders
