"""Atomic paper cash, signal-consumption, position, and journal writes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from typing import Any

import aiosqlite

from execution.indian_charges import IndianEquityChargeModel
from services.paper_bot.risk import DailyRiskState


class PaperExecutionRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def initialize_account(self, initial_cash: Decimal) -> None:
        await self.conn.execute("INSERT OR IGNORE INTO beta_paper_account(singleton,cash) VALUES(1,?)", (str(initial_cash),))
        await self.conn.commit()

    async def cash(self) -> Decimal:
        async with self.conn.execute("SELECT cash FROM beta_paper_account WHERE singleton=1") as cursor:
            row = await cursor.fetchone()
        return Decimal(row[0]) if row else Decimal("0")

    async def is_signal_consumed(self, signal_id: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM beta_consumed_signals WHERE signal_id=?", (signal_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def open_position_once(self, signal_id: str, position: dict[str, Any], cash_debit: Decimal) -> bool:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            async with self.conn.execute("SELECT 1 FROM beta_consumed_signals WHERE signal_id=?", (signal_id,)) as cursor:
                if await cursor.fetchone():
                    await self.conn.rollback()
                    return False
            balance = await self.cash()
            if balance < cash_debit:
                await self.conn.rollback()
                return False
            now = datetime.now(timezone.utc).isoformat()
            await self.conn.execute("INSERT INTO beta_consumed_signals(signal_id,consumed_at) VALUES(?,?)", (signal_id, now))
            await self.conn.execute(
                """INSERT INTO beta_positions
                (id,signal_id,symbol,exchange,quantity,entry_price,charges,charge_breakdown,opened_at,status)
                VALUES(?,?,?,?,?,?,?,?,?, 'OPEN')""",
                (position["id"], signal_id, position["symbol"], position["exchange"],
                 position["quantity"], str(position["entry_price"]), str(position["charges"]),
                 position.get("charge_breakdown", "{}"), position["opened_at"]),
            )
            await self.conn.execute("UPDATE beta_paper_account SET cash=? WHERE singleton=1", (str(balance - cash_debit),))
            await self.conn.commit()
            return True
        except Exception:
            await self.conn.rollback()
            raise

    async def position_count(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) FROM beta_positions") as cursor:
            row = await cursor.fetchone()
        return int(row[0])

    async def get_open_positions(self) -> list[dict[str, Any]]:
        async with self.conn.execute("SELECT * FROM beta_positions WHERE status='OPEN'") as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_daily_risk_state(self, state: DailyRiskState, updated_at: datetime) -> None:
        await self.conn.execute(
            """INSERT INTO beta_paper_daily_risk
            (trading_date,realized_pnl,unrealized_pnl,charges,consecutive_losses,state,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(trading_date) DO UPDATE SET
            realized_pnl=excluded.realized_pnl, unrealized_pnl=excluded.unrealized_pnl,
            charges=excluded.charges, consecutive_losses=excluded.consecutive_losses,
            state=excluded.state, updated_at=excluded.updated_at""",
            (state.trading_date.isoformat(), str(state.realized_pnl), str(state.unrealized_pnl),
             str(state.charges), state.consecutive_losses, state.state, updated_at.isoformat()),
        )
        await self.conn.commit()

    async def get_daily_risk_state(
        self, trading_date: date, marks: dict[str, Decimal]
    ) -> DailyRiskState:
        date_key = trading_date.isoformat()
        async with self.conn.execute(
            "SELECT * FROM beta_paper_daily_risk WHERE trading_date=?", (date_key,)
        ) as cursor:
            persisted = await cursor.fetchone()
        if persisted and persisted["state"] in {"ENTRY_BLOCKED", "EMERGENCY_STOP"}:
            row = dict(persisted)
            return DailyRiskState(
                trading_date, Decimal(row["realized_pnl"]), Decimal(row["unrealized_pnl"]),
                Decimal(row["charges"]), int(row["consecutive_losses"]), row["state"],
            )

        async with self.conn.execute(
            "SELECT * FROM beta_journal WHERE substr(exit_timestamp,1,10)=? ORDER BY exit_timestamp",
            (date_key,),
        ) as cursor:
            journals = [dict(row) for row in await cursor.fetchall()]
        realized = sum(
            (((Decimal(row["exit_price"]) - Decimal(row["entry_price"])) * int(row["quantity"])) for row in journals),
            Decimal("0"),
        )
        charges = sum(
            ((Decimal(row["entry_charges"]) + Decimal(row["exit_charges"])) for row in journals),
            Decimal("0"),
        )
        consecutive = 0
        for row in reversed(journals):
            if Decimal(row["net_pnl"]) < 0:
                consecutive += 1
            else:
                break
        positions = await self.get_open_positions()
        unrealized = Decimal("0")
        for position in positions:
            canonical = f"{position['exchange']}:{position['symbol']}"
            mark = marks.get(canonical)
            if mark is not None:
                unrealized += (Decimal(mark) - Decimal(position["entry_price"])) * int(position["quantity"])
            if str(position["opened_at"]).startswith(date_key):
                charges += Decimal(position["charges"])
        state_name = str(persisted["state"]) if persisted else "ACTIVE"
        prior_losses = int(persisted["consecutive_losses"]) if persisted else 0
        return DailyRiskState(
            trading_date, realized, unrealized, charges, max(consecutive, prior_losses), state_name
        )


    async def close_position(self, position_id: str, exit_price: Decimal, strategy: str, closed_at: datetime) -> dict[str, Any]:
        async with self.conn.execute("SELECT * FROM beta_positions WHERE id=? AND status='OPEN'", (position_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise ValueError("POSITION_NOT_OPEN")
        data = dict(row)
        quantity = int(data["quantity"])
        entry = Decimal(data["entry_price"])
        entry_charges = Decimal(data["charges"])
        exit_breakdown = IndianEquityChargeModel().estimate(
            "SELL", Decimal(exit_price), quantity, data["exchange"]
        )
        exit_charges = exit_breakdown.total
        net_pnl = (Decimal(exit_price) - entry) * quantity - entry_charges - exit_charges
        charges_json = json.dumps({
            "entry": json.loads(data.get("charge_breakdown") or "{}"),
            "exit": {key: str(value) for key, value in exit_breakdown.as_dict().items()},
        })
        journal_id = f"journal-{position_id}"
        await self.conn.execute("UPDATE beta_positions SET status='CLOSED' WHERE id=?", (position_id,))
        await self.conn.execute(
            """INSERT OR IGNORE INTO beta_journal
            (id,position_id,symbol,exchange,strategy,quantity,entry_price,exit_price,net_pnl,
             entry_charges,exit_charges,charges_json,exit_timestamp)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (journal_id, position_id, data["symbol"], data["exchange"], strategy, quantity,
             str(entry), str(exit_price), str(net_pnl), str(entry_charges), str(exit_charges),
             charges_json, closed_at.isoformat()),
        )
        proceeds = Decimal(exit_price) * quantity - exit_charges
        balance = await self.cash()
        await self.conn.execute("UPDATE beta_paper_account SET cash=? WHERE singleton=1", (str(balance + proceeds),))
        await self.conn.commit()
        return {"journal_id": journal_id, "net_pnl": net_pnl, "exit_charges": exit_charges}
