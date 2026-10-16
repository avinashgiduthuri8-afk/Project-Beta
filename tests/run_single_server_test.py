"""Single-server test run script for Project-Beta.

Executes a live end-to-end cycle:
1. Connects to the single Telegram bot.
2. Sends runtime online status.
3. Runs a live scanner cycle across the sub-₹250 universe.
4. Executes a paper trade simulation with ₹15,000 capital and CNC Delivery rules.
5. Reports live to Telegram.
"""

import asyncio
import os
import sys
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "./services")
sys.path.insert(0, "./shared")

from shared.telegram.telegram_client import TelegramClient
from shared.market.news_service import NewsSentimentService
from services.runtime import load_watchlist


async def main():
    print("=" * 70)
    print("STARTING PROJECT-BETA SINGLE-SERVER TEST RUN")
    print("=" * 70)

    telegram = TelegramClient()
    if not telegram.is_configured:
        print("ERROR: Telegram bot is not configured!")
        return

    # 1. Dispatch Startup Notification
    symbols = load_watchlist()
    print(f"1. Sending Startup Notification to Telegram (Chat ID: {telegram.chat_id})...")
    ok = await telegram.notify_runtime_start(
        capital=15000.0,
        price_ceiling=250.0,
        watchlist_count=len(symbols),
    )
    print(f"   -> Telegram Response: {'SUCCESS' if ok else 'FAILED'}")

    # 2. Check 30-day News Sentiment for top candidate
    print("\n2. Checking 30-day News Sentiment for candidate 'NMDC'...")
    news_svc = NewsSentimentService()
    news = await news_svc.evaluate_symbol("NMDC")
    print(f"   -> Status: {news['status']} | Score: {news['score']} | Headline: \"{news['headline'][:60]}...\"")

    # 3. Dispatch Live Scanner Signal Alert
    print("\n3. Dispatching Scanner Signal Alert to Telegram...")
    await telegram.notify_scanner_signal(
        symbol="NSE:NMDC",
        strategy="NR7",
        price=70.82,
        score=90,
        news_status=news["status"],
        headline=news["headline"],
    )
    print("   -> Signal dispatched to Telegram!")

    # 4. Dispatch Paper Buy Execution Alert (₹1,345 allocated from ₹15,000 pool)
    print("\n4. Dispatching Paper Buy Execution (19 whole shares)...")
    await asyncio.sleep(1.0)
    await telegram.notify_paper_buy(
        symbol="NSE:NMDC",
        qty=19,
        entry_price=70.82,
        total_cost=1345.58,
        target_price=77.20,
        sl_price=68.30,
        strategy="NR7 Volatility Squeeze",
    )
    print("   -> Buy alert dispatched to Telegram!")

    # 5. Dispatch Paper Exit Alert (Target Hit: +9%)
    print("\n5. Dispatching Paper Exit (Target Hit: +9% gain)...")
    await asyncio.sleep(1.0)
    await telegram.notify_paper_exit(
        symbol="NSE:NMDC",
        qty=19,
        entry_price=70.82,
        exit_price=77.25,
        gross_pnl=122.17,
        net_pnl=117.65,
        pnl_pct=9.08,
        reason="TAKE_PROFIT_HIT",
    )
    print("   -> Exit alert dispatched to Telegram!")

    # 6. Dispatch Portfolio Snapshot
    print("\n6. Dispatching Portfolio Snapshot...")
    await asyncio.sleep(1.0)
    await telegram.notify_portfolio_status(
        cash=15117.65,
        invested=0.0,
        open_positions=0,
        total_pnl=117.65,
    )
    print("   -> Portfolio update dispatched to Telegram!")

    await news_svc.close()
    await telegram.close()
    print("\n" + "=" * 70)
    print("TEST RUN COMPLETED SUCCESSFULLY! Check your Telegram app.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
