"""Local process runners for the three Project Beta services."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import aiosqlite

from core.config import get_config
from core.repository.beta_store import BetaStore
from core.repository.paper_execution_repo import PaperExecutionRepository
from core.repository.quote_repo import QuoteRepository
from services.learner.indian_service import IndianLearnerService
from market.models import MarketQuote
from market.dhan_feed import DhanMarketFeed
from market.instruments import DhanInstrumentMaster
from market.provider import DhanMarketDataProvider, YahooMarketDataProvider
from market.symbols import StockSymbol
from services.paper_bot.indian_service import IndianPaperService
from services.scanner.indian_service import IndianScannerService

DEFAULT_UNIVERSE = ("NSE:RELIANCE", "NSE:HDFCBANK", "NSE:TCS", "NSE:INFY", "NSE:ICICIBANK")


async def _connection(path: str, read_only: bool = False) -> aiosqlite.Connection:
    if not read_only:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
    else:
        conn = await aiosqlite.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = aiosqlite.Row
    return conn


async def run_scanner() -> None:
    cfg = get_config()
    cfg.validate_v1_safety()
    conn = await _connection(cfg.scanner_db_path)
    store = BetaStore(conn)
    await store.initialize()
    symbols = tuple(map(StockSymbol.parse, DEFAULT_UNIVERSE))
    if cfg.market_data_provider == "DHAN":
        master = DhanInstrumentMaster()
        await master.refresh()
        symbols = tuple(master.resolve(symbol) for symbol in symbols)
        feed = DhanMarketFeed(cfg.dhan_client_id or "", cfg.dhan_access_token or "")
        await feed.start(symbols, lambda quote: asyncio.sleep(0))
        provider = DhanMarketDataProvider(feed, YahooMarketDataProvider())
    else:
        provider = YahooMarketDataProvider()
    scanner = IndianScannerService(provider, store, symbols, quote_repo=QuoteRepository(conn))
    try:
        while True:
            await scanner.poll()
            await asyncio.sleep(cfg.scanner_poll_interval)
    finally:
        await provider.close()
        await conn.close()


async def run_paper() -> None:
    cfg = get_config()
    cfg.validate_v1_safety()
    scanner_conn = await _connection(cfg.scanner_db_path, read_only=True)
    paper_conn = await _connection(cfg.paper_db_path)
    paper_store = BetaStore(paper_conn)
    await paper_store.initialize()
    repo = PaperExecutionRepository(paper_conn)
    await repo.initialize_account(Decimal(str(cfg.initial_paper_cash)))
    service = IndianPaperService(repo, Decimal(str(cfg.order_size_inr)))
    quotes = QuoteRepository(scanner_conn)
    try:
        while True:
            async with scanner_conn.execute("SELECT * FROM beta_signals WHERE status='QUALIFIED' ORDER BY market_timestamp") as cursor:
                signals = [dict(row) for row in await cursor.fetchall()]
            for signal in signals:
                quote = await quotes.get_latest(f"{signal['exchange']}:{signal['symbol']}")
                if quote:
                    await service.handle_signal({**signal, "mode": "PAPER"}, quote, datetime.now(timezone.utc))
            # Continuously monitor active swing positions for SL/TP exit triggers
            await service.evaluate_open_positions(quotes, datetime.now(timezone.utc))
            await asyncio.sleep(cfg.paper_poll_interval)
    finally:
        await scanner_conn.close()
        await paper_conn.close()


async def run_learner() -> None:
    cfg = get_config()
    cfg.validate_v1_safety()
    paper_conn = await _connection(cfg.paper_db_path, read_only=True)
    learner_conn = await _connection(cfg.learner_db_path)
    learner_store = BetaStore(learner_conn)
    await learner_store.initialize()
    service = IndianLearnerService(paper_conn, cfg.learner_min_trades, write_conn=learner_conn)
    try:
        while True:
            await service.run_cycle()
            await asyncio.sleep(cfg.learner_poll_interval)
    finally:
        await paper_conn.close()
        await learner_conn.close()
