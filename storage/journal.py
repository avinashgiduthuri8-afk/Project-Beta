"""Trade audit journal with CSV export and daily summaries."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List
from datetime import date
from core.models import Trade


class Journal:
    """Logs executed trades to a CSV file for compliance and post-trade performance analytics."""

    def __init__(self, csv_path: str = "storage/trade_journal.csv"):
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Trade_ID", "Order_ID", "Symbol", "Side",
                    "Quantity", "Price", "Value", "Timestamp"
                ])

    def log_trade(self, trade: Trade) -> None:
        """Append trade record to CSV journal."""
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.trade_id,
                trade.order_id,
                trade.symbol,
                trade.side.value,
                trade.quantity,
                f"{trade.price:.2f}",
                f"{trade.value:.2f}",
                trade.timestamp.isoformat(),
            ])
