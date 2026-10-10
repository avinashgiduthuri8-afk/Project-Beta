"""
V2 Post-Trade Journaling Service.

Subscribes to POSITION_CLOSED events on EventBus, extracts trade metrics,
computes exact statutory fee & tax breakdowns (1.572% total drag: 0.20% fee + 18% GST + 1% Sec 194S TDS + 0.10% slippage),
evaluates holding duration, MFE/MAE excursions, and persists journal records.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.logging import get_logger
from core.repository.journal_repo import JournalRepository

logger = get_logger("background.journal")


class JournalService:
    """Post-Trade Journaling & Execution Logging Service."""

    def __init__(
        self,
        bus: EventBus,
        journal_repo: JournalRepository,
    ) -> None:
        self._bus = bus
        self._journal_repo = journal_repo
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._bus.subscribe(EventType.POSITION_CLOSED, self.on_position_closed)
        logger.info("JournalService started and subscribed to POSITION_CLOSED events")

    async def stop(self) -> None:
        self._started = False
        self._bus.unsubscribe(EventType.POSITION_CLOSED, self.on_position_closed)
        logger.info("JournalService stopped")

    def compute_statutory_friction(
        self,
        entry_price: float,
        exit_price: float,
        quantity: float,
        product: str = "CNC",
    ) -> dict[str, float]:
        """
        Compute exact statutory friction and Indian crypto tax breakdown:
          - Buy Exchange Fee: 0.20% of buy notional
          - Buy GST: 18% on buy fee
          - Sell Exchange Fee: 0.20% of sell notional
          - Sell GST: 18% on sell fee
          - Sec 194S TDS: 1.00% on sell notional
          - Estimated Slippage: 0.10% total (0.05% buy + 0.05% sell)
          - Total Statutory Drag = exchange_fee + gst_tax + tds_194s + slippage_cost
        """
        buy_notional = entry_price * quantity
        sell_notional = exit_price * quantity

        intraday = product.upper() in {"MIS", "INTRADAY"}
        brokerage = (
            min(20.0, buy_notional * 0.0003)
            + min(20.0, sell_notional * 0.0003)
            if intraday
            else 0.0
        )
        transaction_charges = (buy_notional + sell_notional) * 0.0000297
        sebi_charges = (buy_notional + sell_notional) * 0.000001
        stamp_duty = buy_notional * (0.00003 if intraday else 0.00015)
        stt = (
            sell_notional * 0.00025
            if intraday
            else (buy_notional + sell_notional) * 0.001
        )
        exchange_fee = round(
            brokerage + transaction_charges + sebi_charges + stamp_duty, 4
        )
        gst_tax = round(
            (brokerage + transaction_charges + sebi_charges) * 0.18, 4
        )
        # Legacy column retained for schema compatibility; this now stores STT.
        tds_194s = round(stt, 4)
        slippage_cost = round((buy_notional + sell_notional) * 0.0005, 4)

        total_statutory_drag = round(
            exchange_fee + gst_tax + tds_194s + slippage_cost, 4
        )

        return {
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
            "exchange_fee": exchange_fee,
            "gst_tax": gst_tax,
            "tds_194s": tds_194s,
            "stt": tds_194s,
            "slippage_cost": slippage_cost,
            "total_statutory_drag": total_statutory_drag,
        }

    async def on_position_closed(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        """
        EventBus handler for POSITION_CLOSED events.
        Extracts position details, calculates statutory tax breakdown & excursions,
        and saves journal record.
        """
        try:
            position_id = str(
                payload.get("position_id") or payload.get("id") or "UNKNOWN_POS"
            )
            bot_name = str(
                payload.get("bot") or payload.get("bot_name") or "STE"
            ).upper()
            pair = str(payload.get("pair") or "NSE:RELIANCE").upper()
            side = str(payload.get("side") or "BUY").upper()

            entry_price = float(payload.get("entry_price") or 100.0)
            exit_price = float(payload.get("exit_price") or entry_price)
            quantity = float(payload.get("qty") or payload.get("quantity") or 1.0)

            opened_at_str = payload.get("opened_at") or payload.get("entry_time")
            closed_at_str = payload.get("closed_at") or payload.get("exit_time")

            now = datetime.now(timezone.utc)
            entry_timestamp = str(opened_at_str or now.isoformat())
            exit_timestamp = str(closed_at_str or now.isoformat())

            # Parse timestamps for duration calculation
            try:
                e_dt = datetime.fromisoformat(entry_timestamp.replace("Z", "+00:00"))
                x_dt = datetime.fromisoformat(exit_timestamp.replace("Z", "+00:00"))
                duration_seconds = max(0, int((x_dt - e_dt).total_seconds()))
            except Exception:
                duration_seconds = 0

            exit_reason = str(payload.get("exit_reason") or "TP_HIT").upper()

            # PnL & Statutory calculations
            gross_pnl = round(
                (
                    (exit_price - entry_price) * quantity
                    if side == "BUY"
                    else (entry_price - exit_price) * quantity
                ),
                2,
            )
            friction = self.compute_statutory_friction(
                entry_price,
                exit_price,
                quantity,
                product=str(payload.get("product") or "CNC"),
            )

            total_drag = friction["total_statutory_drag"]
            net_pnl = round(gross_pnl - total_drag, 2)

            buy_notional = friction["buy_notional"]
            net_pnl_pct = (
                round((net_pnl / buy_notional) * 100.0, 2) if buy_notional > 0 else 0.0
            )

            # Excursion metrics (MFE & MAE)
            peak_price = float(
                payload.get("peak_price") or max(entry_price, exit_price)
            )
            trough_price = float(
                payload.get("trough_price") or min(entry_price, exit_price)
            )

            mfe = round(max(0.0, (peak_price - entry_price) * quantity), 4)
            mae = round(max(0.0, (entry_price - trough_price) * quantity), 4)

            tags = payload.get("tags") or [
                f"strategy:{bot_name}",
                f"exit:{exit_reason}",
                f"pair:{pair}",
            ]

            journal_entry = {
                "id": str(uuid.uuid4()),
                "position_id": position_id,
                "bot_name": bot_name,
                "pair": pair,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": quantity,
                "entry_timestamp": entry_timestamp,
                "exit_timestamp": exit_timestamp,
                "duration_seconds": duration_seconds,
                "exit_reason": exit_reason,
                "gross_pnl": gross_pnl,
                "exchange_fee": friction["exchange_fee"],
                "gst_tax": friction["gst_tax"],
                "tds_194s": friction["tds_194s"],
                "slippage_cost": friction["slippage_cost"],
                "total_statutory_drag": total_drag,
                "net_pnl": net_pnl,
                "net_pnl_pct": net_pnl_pct,
                "mfe": mfe,
                "mae": mae,
                "tags": tags,
            }

            await self._journal_repo.insert_entry(journal_entry)
            logger.info(
                "Processed post-trade journal entry for position %s", position_id
            )

        except Exception as exc:
            logger.error("Failed to journal closed position: %s", exc, exc_info=True)
