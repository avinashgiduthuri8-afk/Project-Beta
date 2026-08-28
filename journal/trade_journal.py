"""Comprehensive Trade Audit Journal for Indian Equities (Project-Beta)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from core.models import TradePlan, Order, Trade


class TradeJournal:
    """Stores full audit logs of trade setups, scorecards, AI metrics, and P&L outcomes."""

    def __init__(self, db_path: str = "storage/trades.db", csv_path: str = "storage/trade_journal.csv"):
        self.db_path = Path(db_path)
        self.csv_path = Path(csv_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._init_csv()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_journal (
                    plan_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    setup_type TEXT,
                    total_score REAL,
                    trend_score REAL,
                    geometry_score REAL,
                    volume_score REAL,
                    mtf_score REAL,
                    ai_confidence REAL,
                    entry_price REAL,
                    stop_loss REAL,
                    target_price REAL,
                    exit_price REAL,
                    quantity INTEGER,
                    realized_pnl REAL,
                    fees_stt_gst REAL,
                    net_pnl REAL,
                    status TEXT,
                    entry_time TEXT,
                    exit_time TEXT
                )
            """)
            conn.commit()

    def _init_csv(self) -> None:
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Plan_ID", "Symbol", "Setup_Type", "Score", "AI_Confidence",
                    "Entry", "SL", "Target", "Exit", "Qty", "Gross_PnL", "Fees", "Net_PnL", "Status", "Timestamp"
                ])

    def log_trade_entry(self, plan: TradePlan, order: Order) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trade_journal (
                    plan_id, symbol, setup_type, total_score, ai_confidence,
                    entry_price, stop_loss, target_price, quantity, status, entry_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plan.plan_id, plan.symbol, plan.setup_type.value, 85.0, plan.ai_confidence,
                order.average_price or plan.entry_price, plan.stop_loss, plan.target_price,
                plan.calculated_quantity, "OPEN", datetime.now().isoformat(),
            ))
            conn.commit()

    def log_trade_exit(self, plan_id: str, exit_price: float, exit_order: Order) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, setup_type, entry_price, quantity FROM trade_journal WHERE plan_id = ?", (plan_id,))
            row = cursor.fetchone()
            if not row:
                return

            symbol, setup_type, entry_p, qty = row
            gross_pnl = round((exit_price - entry_p) * qty, 2)
            
            # Statutory charges: ~0.05% of turnover (STT + Exchange txn + SEBI + GST)
            turnover = (entry_p + exit_price) * qty
            est_fees = round(turnover * 0.0005, 2)
            net_pnl = round(gross_pnl - est_fees, 2)

            cursor.execute("""
                UPDATE trade_journal SET
                    exit_price = ?,
                    realized_pnl = ?,
                    fees_stt_gst = ?,
                    net_pnl = ?,
                    status = 'CLOSED',
                    exit_time = ?
                WHERE plan_id = ?
            """, (exit_price, gross_pnl, est_fees, net_pnl, datetime.now().isoformat(), plan_id))
            conn.commit()

            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    plan_id, symbol, setup_type, "85.0", "0.85",
                    f"{entry_p:.2f}", "", "", f"{exit_price:.2f}", qty,
                    f"{gross_pnl:.2f}", f"{est_fees:.2f}", f"{net_pnl:.2f}", "CLOSED", datetime.now().isoformat()
                ])
