"""
V2 PortfolioAggregator — computes cross-bot capital allocation, PnL, and AUM.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.types import BotName, PortfolioSnapshot, Position, Trade


def _is_non_inr_pair(pair: str | None) -> bool:
    if not pair:
        return False
    p = pair.upper()
    return (
        p.endswith("USDT")
        or p.endswith("/USDT")
        or p.endswith("_USDT")
        or p.endswith("USD")
        or p.endswith("/USD")
        or p.endswith("_USD")
    )


class PortfolioAggregator:
    """Pure mathematical aggregator for multi-bot portfolio state."""

    @staticmethod
    def aggregate(
        positions: list[Position],
        closed_trades: list[Trade],
        base_cash: float = 100000.0,
        fx_rate: float = 91.50,
    ) -> PortfolioSnapshot:
        """Calculate live AUM, deployed capital, cash, and PnL breakdown."""
        positions_by_bot: dict[str, list[Position]] = {
            bot.value: [] for bot in BotName
        }

        total_deployed = 0.0
        total_mtm = 0.0
        total_unrealised = 0.0

        for pos in positions:
            b_key = pos.bot.value if isinstance(pos.bot, BotName) else str(pos.bot)
            if b_key not in positions_by_bot:
                positions_by_bot[b_key] = []
            positions_by_bot[b_key].append(pos)

            deployed = pos.deployed_capital
            total_deployed += deployed

            mark_price = (
                pos.current_price
                if (pos.current_price is not None and pos.current_price > 0)
                else pos.entry_price
            )
            rate = fx_rate if _is_non_inr_pair(pos.pair) else 1.0

            mtm_val = pos.qty * mark_price * rate
            total_mtm += mtm_val

            if pos.unrealised_pnl is not None:
                total_unrealised += pos.unrealised_pnl * rate
            else:
                total_unrealised += (mark_price - pos.entry_price) * pos.qty * rate

        total_realised = sum(
            t.pnl * (fx_rate if _is_non_inr_pair(t.pair) else 1.0)
            for t in closed_trades
        )

        # Cash = Initial Cash + Realised PnL - Currently Deployed Capital
        total_cash = max(0.0, base_cash + total_realised - total_deployed)
        total_aum = total_cash + total_deployed + total_unrealised

        capital_util = (
            round((total_deployed / total_aum * 100.0), 2) if total_aum > 0 else 0.0
        )

        # Calculate daily realised PnL (from today UTC)
        today_utc = datetime.now(timezone.utc).date()
        daily_pnl = (
            sum(
                t.pnl * (fx_rate if _is_non_inr_pair(t.pair) else 1.0)
                for t in closed_trades
                if hasattr(t, "exit_time")
                and t.exit_time
                and hasattr(t.exit_time, "date")
                and t.exit_time.date() == today_utc
            )
            + total_unrealised
        )

        return PortfolioSnapshot(
            total_aum=round(total_aum, 2),
            total_deployed=round(total_deployed, 2),
            total_cash=round(total_cash, 2),
            total_unrealised_pnl=round(total_unrealised, 2),
            total_realised_pnl=round(total_realised, 2),
            daily_pnl=round(daily_pnl, 2),
            capital_utilisation=capital_util,
            positions_by_bot=positions_by_bot,
            captured_at=datetime.now(timezone.utc),
        )
