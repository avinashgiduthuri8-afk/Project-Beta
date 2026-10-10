"""
V2 Statutory Tax & Compliance Ledger Service.

Calculates Section 194S TDS collections, GST brokerage taxes, and statutory compliance
reports for Indian crypto tax regulation and financial year reporting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.logging import get_logger
from core.repository.journal_repo import JournalRepository

logger = get_logger("background.analytics.tax_ledger")


class TaxLedgerService:
    """Statutory Tax & Compliance Ledger Engine."""

    def __init__(self, journal_repo: JournalRepository) -> None:
        self._journal_repo = journal_repo

    async def get_tax_summary(
        self,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate statutory tax and compliance ledger summary over given date range.
        If start_iso/end_iso are omitted, fetches all recorded trade journal entries.
        """
        if start_iso and end_iso:
            entries = await self._journal_repo.get_entries_by_timerange(
                start_iso, end_iso
            )
        else:
            entries = await self._journal_repo.get_all_journal_entries()

        total_trades = len(entries)
        gross_trading_value = 0.0
        total_exchange_fees = 0.0
        total_gst = 0.0
        total_tds_194s = 0.0
        total_slippage = 0.0
        total_statutory_drag = 0.0
        gross_pnl = 0.0
        net_pnl = 0.0

        for e in entries:
            entry_price = float(e.get("entry_price", 0.0))
            exit_price = float(e.get("exit_price", 0.0))
            qty = float(e.get("quantity", 0.0))

            buy_notional = entry_price * qty
            sell_notional = exit_price * qty
            gross_trading_value += buy_notional + sell_notional

            total_exchange_fees += float(e.get("exchange_fee", 0.0))
            total_gst += float(e.get("gst_tax", 0.0))
            total_tds_194s += float(e.get("tds_194s", 0.0))
            total_slippage += float(e.get("slippage_cost", 0.0))
            total_statutory_drag += float(e.get("total_statutory_drag", 0.0))
            gross_pnl += float(e.get("gross_pnl", 0.0))
            net_pnl += float(e.get("net_pnl", 0.0))

        # Quarterly breakdown
        quarters = self._compute_quarterly_breakdown(entries)

        return {
            "period": {
                "start": start_iso or "ALL_TIME",
                "end": end_iso or "ALL_TIME",
            },
            "total_trades": total_trades,
            "gross_trading_value_inr": round(gross_trading_value, 2),
            "total_exchange_fees_inr": round(total_exchange_fees, 2),
            "total_gst_inr": round(total_gst, 2),
            "total_stt_inr": round(total_tds_194s, 2),
            "total_slippage_cost_inr": round(total_slippage, 2),
            "total_statutory_drag_inr": round(total_statutory_drag, 2),
            "gross_pnl_inr": round(gross_pnl, 2),
            "net_pnl_inr": round(net_pnl, 2),
            "quarterly_breakdown": quarters,
            "tax_compliance_notes": [
                "Legacy tds_194s storage is interpreted as Securities Transaction Tax (STT)",
                "GST is estimated on brokerage, transaction, and SEBI charges",
                "Contract notes are authoritative; income tax is not computed",
            ],
        }

    def _compute_quarterly_breakdown(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """
        Group entries into Indian Financial Year Quarters:
          Q1: Apr-Jun
          Q2: Jul-Sep
          Q3: Oct-Dec
          Q4: Jan-Mar
        """
        q_map: dict[str, dict[str, float]] = {
            "Q1_Apr_Jun": {"trades": 0, "tds_194s": 0.0, "gst": 0.0, "net_pnl": 0.0},
            "Q2_Jul_Sep": {"trades": 0, "tds_194s": 0.0, "gst": 0.0, "net_pnl": 0.0},
            "Q3_Oct_Dec": {"trades": 0, "tds_194s": 0.0, "gst": 0.0, "net_pnl": 0.0},
            "Q4_Jan_Mar": {"trades": 0, "tds_194s": 0.0, "gst": 0.0, "net_pnl": 0.0},
        }

        for e in entries:
            ts_str = str(e.get("exit_timestamp", ""))
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                m = dt.month
                if m in (4, 5, 6):
                    key = "Q1_Apr_Jun"
                elif m in (7, 8, 9):
                    key = "Q2_Jul_Sep"
                elif m in (10, 11, 12):
                    key = "Q3_Oct_Dec"
                else:
                    key = "Q4_Jan_Mar"
            except Exception:
                key = "Q1_Apr_Jun"

            q_map[key]["trades"] += 1
            q_map[key]["tds_194s"] += float(e.get("tds_194s", 0.0))
            q_map[key]["gst"] += float(e.get("gst_tax", 0.0))
            q_map[key]["net_pnl"] += float(e.get("net_pnl", 0.0))

        # Format numbers cleanly
        formatted = {}
        for q, vals in q_map.items():
            formatted[q] = {
                "trades": int(vals["trades"]),
                "stt_inr": round(vals["tds_194s"], 2),
                "gst_inr": round(vals["gst"], 2),
                "net_pnl_inr": round(vals["net_pnl"], 2),
            }
        return formatted
