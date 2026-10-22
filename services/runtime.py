"""Local process runners for the three Project Beta services.

Supports running services independently or concurrently on a single server runtime
with automated Telegram alerts, 30-day news safety checks, and dynamic sub-₹250 stock execution.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

from core.config import get_config
from core.database import open_beta_database
from core.migrations import initialize_beta_schema
from core.repository.beta_store import BetaStore
from core.repository.paper_execution_repo import PaperExecutionRepository
from core.repository.quote_repo import QuoteRepository
from services.learner.indian_service import IndianLearnerService
from market.models import MarketQuote
from market.dhan_feed import DhanMarketFeed
from market.instruments import DhanInstrumentMaster
from market.holidays import HolidayCalendar
from market.news_service import NewsSentimentService
from market.provider import DhanMarketDataProvider, TradingViewMarketDataProvider, YahooMarketDataProvider
from market.symbols import StockSymbol
from market.tradingview_client import TradingViewPublicClient
from services.paper_bot.indian_service import IndianPaperService
from services.paper_bot.risk import PaperRiskPolicy
from services.scanner.indian_service import IndianScannerService
from services.scanner.eligibility import EligibilityEngine
from shared.telegram.telegram_client import TelegramClient

DEFAULT_UNIVERSE = (
    "NSE:ASHOKLEY", "NSE:TATASTEEL", "NSE:NMDC", "NSE:MOTHERSON",
    "NSE:SAIL", "NSE:RVNL", "NSE:POWERGRID", "NSE:GMRAIRPORT",
    "NSE:GAIL", "NSE:ONGC", "NSE:CANBK", "NSE:PNB",
    "NSE:UNIONBANK", "NSE:BANKBARODA", "NSE:IDFCFIRSTB", "NSE:IRFC"
)


def load_watchlist() -> tuple[StockSymbol, ...]:
    path = Path("config/nse_watchlist.json")
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                exchange = data.get("exchange", "NSE")
                symbols = data.get("symbols", [])
                if symbols:
                    return tuple(StockSymbol.parse(f"{exchange}:{s}") for s in symbols)
        except Exception:
            pass
    return tuple(map(StockSymbol.parse, DEFAULT_UNIVERSE))


async def initialize_runtime_database(path: str) -> None:
    conn = await open_beta_database(path)
    try:
        await initialize_beta_schema(conn)
    finally:
        await conn.close()


async def run_scanner(telegram_client: TelegramClient | None = None) -> None:
    cfg = get_config()
    cfg.validate_v1_safety()
    await initialize_runtime_database(cfg.db_path)
    conn = await open_beta_database(cfg.db_path)
    store = BetaStore(conn)
    await store.initialize()
    configured_symbols = load_watchlist()
    master = DhanInstrumentMaster()
    await master.refresh()
    allowed = {item.symbol.canonical: item for item in master.nse_universe()}
    instruments = {
        symbol.canonical: allowed[symbol.canonical]
        for symbol in configured_symbols
        if symbol.canonical in allowed
    }
    symbols = tuple(item.symbol for item in instruments.values())
    if not symbols:
        raise RuntimeError("No configured symbols passed the current NSE instrument master")

    if cfg.market_data_provider == "DHAN":
        feed = DhanMarketFeed(cfg.dhan_client_id or "", cfg.dhan_access_token or "")
        await feed.start(symbols, lambda quote: asyncio.sleep(0))
        provider = DhanMarketDataProvider(feed, YahooMarketDataProvider())
    elif cfg.market_data_provider == "TRADINGVIEW":
        tv_client = TradingViewPublicClient(api_key=cfg.tradingview_api_key)
        provider = TradingViewMarketDataProvider(tv_client, candle_provider=YahooMarketDataProvider())
    else:
        provider = YahooMarketDataProvider()

    telegram = telegram_client or TelegramClient()
    news = NewsSentimentService()
    scanner = IndianScannerService(
        provider,
        store,
        symbols,
        quote_repo=QuoteRepository(conn),
        price_ceiling=Decimal(str(cfg.price_ceiling_inr)),
        price_floor=Decimal(str(cfg.price_floor_inr)),
        news_service=news,
        telegram_client=telegram,
        eligibility_engine=EligibilityEngine(price_ceiling=Decimal(str(cfg.price_ceiling_inr))),
        instruments=instruments,
        holiday_calendar=HolidayCalendar.from_json("config/nse_holidays.json"),
    )
    try:
        while True:
            if hasattr(provider, "prefetch_quotes"):
                await provider.prefetch_quotes(symbols)
            await scanner.poll()
            await asyncio.sleep(cfg.scanner_poll_interval)
    finally:
        await news.close()
        await provider.close()
        await conn.close()


async def run_paper(telegram_client: TelegramClient | None = None) -> None:
    cfg = get_config()
    cfg.validate_v1_safety()
    await initialize_runtime_database(cfg.db_path)
    scanner_conn = await open_beta_database(cfg.db_path, read_only=True)
    paper_conn = await open_beta_database(cfg.db_path)
    paper_store = BetaStore(paper_conn)
    await paper_store.initialize()
    repo = PaperExecutionRepository(paper_conn)
    await repo.initialize_account(Decimal(str(cfg.initial_paper_cash)))

    telegram = telegram_client or TelegramClient()
    service = IndianPaperService(
        repo,
        Decimal(str(cfg.order_size_inr)),
        telegram_client=telegram,
        risk_policy=PaperRiskPolicy(
            Decimal(str(cfg.daily_loss_warning_inr)),
            Decimal(str(cfg.daily_entry_block_inr)),
            Decimal(str(cfg.daily_loss_limit_inr)),
            cfg.max_consecutive_losses,
        ),
        max_open_positions=cfg.max_open_positions,
        max_positions_per_sector=cfg.max_positions_per_sector,
    )
    quotes = QuoteRepository(scanner_conn)
    try:
        while True:
            now = datetime.now(timezone.utc)
            await service.enforce_daily_limits(quotes, now)
            async with scanner_conn.execute("SELECT * FROM beta_signals WHERE status='QUALIFIED' ORDER BY market_timestamp") as cursor:
                signals = [dict(row) for row in await cursor.fetchall()]
            for signal in signals:
                quote = await quotes.get_latest(f"{signal['exchange']}:{signal['symbol']}")
                if quote:
                    await service.handle_signal({**signal, "mode": "PAPER"}, quote, now)
            # Continuously monitor active swing positions for SL/TP exit triggers
            await service.evaluate_open_positions(quotes, now)
            await asyncio.sleep(cfg.paper_poll_interval)
    finally:
        await scanner_conn.close()
        await paper_conn.close()


async def run_learner(telegram_client: TelegramClient | None = None) -> None:
    cfg = get_config()
    cfg.validate_v1_safety()
    await initialize_runtime_database(cfg.db_path)
    paper_conn = await open_beta_database(cfg.db_path, read_only=True)
    learner_conn = await open_beta_database(cfg.db_path)
    learner_store = BetaStore(learner_conn)
    await learner_store.initialize()
    service = IndianLearnerService(
        paper_conn,
        cfg.learner_min_trades,
        write_conn=learner_conn,
        telegram_client=telegram_client,
    )
    try:
        while True:
            await service.run_cycle()
            await asyncio.sleep(cfg.learner_poll_interval)
    finally:
        await paper_conn.close()
        await learner_conn.close()


async def run_all() -> None:
    """Run all three Project Beta services concurrently in a single process / server."""
    cfg = get_config()
    cfg.validate_v1_safety()
    await initialize_runtime_database(cfg.db_path)
    telegram = TelegramClient()
    symbols = load_watchlist()

    # Announce startup to Telegram
    await telegram.notify_runtime_start(
        capital=cfg.initial_paper_cash,
        price_ceiling=cfg.price_ceiling_inr,
        watchlist_count=len(symbols),
    )

    print("=" * 80)
    print("🚀 PROJECT-BETA SINGLE SERVER RUNTIME ONLINE")
    print(f"💰 Capital: INR {cfg.initial_paper_cash:,.2f}")
    print(f"🎯 Price Range: INR {cfg.price_floor_inr:.0f} - {cfg.price_ceiling_inr:.0f}")
    print(f"📋 Watchlist: {len(symbols)} liquid NSE symbols")
    print(f"⚡ Strategies: STE, HDA, VCP, BBS, NR7")
    print(f"📡 Market Provider: {cfg.market_data_provider}")
    print(f"📱 Telegram Bot: Connected ({telegram.chat_id})")
    print("=" * 80)

    try:
        await asyncio.gather(
            run_scanner(telegram_client=telegram),
            run_paper(telegram_client=telegram),
            run_learner(telegram_client=telegram),
            telegram.start_polling(db_path=cfg.db_path),
        )
    finally:
        await telegram.close()


if __name__ == "__main__":
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if mode == "scanner":
        asyncio.run(run_scanner())
    elif mode == "paper":
        asyncio.run(run_paper())
    elif mode == "learner":
        asyncio.run(run_learner())
    else:
        asyncio.run(run_all())
