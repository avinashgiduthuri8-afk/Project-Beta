"""
Trade Audit Journal & CSV Exporter.
Appends executed trades to a CSV file and calculates end-of-day execution summaries.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from core.models import Trade

logger = logging.getLogger(__name__)


class TradeJournal:
    """CSV-based audit journal for recording all executed trades."""

    CSV_HEADERS = [
        "timestamp",
        "trade_id",
        "order_id",
        "exchange_order_id",
        "symbol",
        "exchange",
        "side",
        "product",
        "price",
        "quantity",
        "turnover",
    ]

    def __init__(self, csv_path: str = "data/trade_journal.csv") -> None:
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADERS)

    def log_trade(self, trade: Trade) -> None:
        """Append trade to CSV journal."""
        turnover = trade.price * trade.quantity
        row = [
            trade.timestamp.isoformat(),
            trade.trade_id,
            trade.order_id,
            trade.exchange_order_id or "",
            trade.symbol,
            trade.exchange.value,
            trade.side.value,
            trade.product.value,
            f"{trade.price:.2f}",
            trade.quantity,
            f"{turnover:.2f}",
        ]
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        logger.info(f"Journaled trade {trade.trade_id}: {trade.side.value} {trade.quantity} {trade.symbol} @ ₹{trade.price:.2f}")

    def generate_daily_summary(self, trades: List[Trade]) -> str:
        """Generate human-readable summary of daily executions."""
        if not trades:
            return "No trades executed today."

        total_trades = len(trades)
        total_turnover = sum(t.price * t.quantity for t in trades)
        buys = sum(1 for t in trades if t.side.value == "BUY")
        sells = sum(1 for t in trades if t.side.value == "SELL")

        summary = (
            f"📊 **Daily Execution Summary**\n"
            f"• Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"• Total Trades: {total_trades} (Buys: {buys}, Sells: {sells})\n"
            f"• Total Turnover: ₹{total_turnover:,.2f}\n"
        )
        return summary
