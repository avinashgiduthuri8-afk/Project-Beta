"""Atomic paper cash, signal-consumption, position, and journal writes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any

import aiosqlite

from execution.indian_charges import IndianEquityChargeModel


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
