import asyncio
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import get_config
from core.repository.candle_repo import CandleRepository
from core.repository.db import Database
from core.repository.event_log_repo import EventLogRepository
from core.repository.position_repo import PositionRepository
from core.repository.signal_repo import SignalRepository
from core.repository.trade_repo import TradeRepository
from scanner.market_context import MarketContextService
from scanner.service import ScannerService


# Dummy MarketContextService to prevent network calls during backtest
class BacktestMarketContextService(MarketContextService):
    def __init__(self, db_connection):
        super().__init__()
        self.db_conn = db_connection
        self._current_time_ms = None

    def set_time(self, ts_ms):
        self._current_time_ms = ts_ms

    async def refresh_market_context(self) -> dict:
        # Evaluate regime from historical candles
        btc_candles = []
        eth_candles = []

        async with self.db_conn.execute(
            "SELECT open, high, low, close, volume, timestamp FROM market_candles WHERE pair=? AND timeframe=? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 30",
            ("BTC/USDT", "15m", self._current_time_ms),
        ) as cursor:
            for r in reversed(await cursor.fetchall()):
                btc_candles.append(
                    {
                        "open": r[0],
                        "high": r[1],
                        "low": r[2],
                        "close": r[3],
                        "volume": r[4],
                        "time": r[5],
                    }
                )

        if not btc_candles:
            async with self.db_conn.execute(
                "SELECT open, high, low, close, volume, timestamp FROM market_candles WHERE pair=? AND timeframe=? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 30",
                ("BTC/INR", "15m", self._current_time_ms),
            ) as cursor:
                for r in reversed(await cursor.fetchall()):
                    btc_candles.append(
                        {
                            "open": r[0],
                            "high": r[1],
                            "low": r[2],
                            "close": r[3],
                            "volume": r[4],
                            "time": r[5],
                        }
                    )

        async with self.db_conn.execute(
            "SELECT open, high, low, close, volume, timestamp FROM market_candles WHERE pair=? AND timeframe=? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 30",
            ("ETH/USDT", "15m", self._current_time_ms),
        ) as cursor:
            for r in reversed(await cursor.fetchall()):
                eth_candles.append(
                    {
                        "open": r[0],
                        "high": r[1],
                        "low": r[2],
                        "close": r[3],
                        "volume": r[4],
                        "time": r[5],
                    }
                )

        if not eth_candles:
            async with self.db_conn.execute(
                "SELECT open, high, low, close, volume, timestamp FROM market_candles WHERE pair=? AND timeframe=? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 30",
                ("ETH/INR", "15m", self._current_time_ms),
            ) as cursor:
                for r in reversed(await cursor.fetchall()):
                    eth_candles.append(
                        {
                            "open": r[0],
                            "high": r[1],
                            "low": r[2],
                            "close": r[3],
                            "volume": r[4],
                            "time": r[5],
                        }
                    )

        btc_trend, eth_trend, regime = self.evaluate_regime(btc_candles, eth_candles)
        self._latest_context = {
            "btc_trend": btc_trend,
            "eth_trend": eth_trend,
            "market_regime": regime,
            "fear_and_greed": 50,
        }
        return self._latest_context


class DummyNewsService:
    async def fetch_latest_news(self):
        pass

    def evaluate_coin_news(self, coin):
        return {"risk_score": 0, "sentiment": "NEUTRAL"}


async def main():
    print("Initializing Database...")
    cfg = get_config()
    db = Database(path="data/project_alpha.db")
    await db.open()

    bus = EventBus()
    candle_repo = CandleRepository(db.connection)
    mc_svc = BacktestMarketContextService(db.connection)
    signal_repo = SignalRepository(db.connection)
    event_repo = EventLogRepository(db.connection)
    pos_repo = PositionRepository(db.connection)
    trade_repo = TradeRepository(db.connection)

    scanner = ScannerService(
        bus=bus,
        signal_repo=signal_repo,
        event_log_repo=event_repo,
        config=cfg,
        candle_repo=candle_repo,
        position_repo=pos_repo,
        trade_repo=trade_repo,
        market_context_service=mc_svc,
    )
    # Mock _fetch_watchlist_coins to return only the pairs we have in DB
    async with db.connection.execute(
        "SELECT DISTINCT pair FROM market_candles"
    ) as cursor:
        db_pairs = [r[0] for r in await cursor.fetchall()]
    async def mock_watchlist():
        return db_pairs

    scanner._fetch_watchlist_coins = mock_watchlist

    scanner._news_risk_service = DummyNewsService()

    # Mock _fetch_coindcx_candles to prevent network requests during backtest
    async def mock_fetch(*args, **kwargs):
        return []

    scanner._fetch_coindcx_candles = mock_fetch

    scanner._started = True

    # We will collect generated signals
    generated_signals = []

    async def on_signal(event_type, payload):
        generated_signals.append(payload)

    bus.subscribe(EventType.SIGNAL_GENERATED, on_signal)

    print("Fetching timeline...")
    # Get distinct 1h timestamps from DB
    async with db.connection.execute(
        "SELECT DISTINCT timestamp FROM market_candles WHERE timeframe='1h' ORDER BY timestamp ASC"
    ) as cursor:
        timestamps = [r[0] for r in await cursor.fetchall()]
    print(f"Timeline: {len(timestamps)} hours.")

    if len(timestamps) < 120:
        print("Not enough history to backtest.")
        return

    start_idx = 21  # 21 candles warm-up for EMA21, RSI14, ATR14

    print(
        f"Running chronological scanner backtest over {len(timestamps) - start_idx} hours..."
    )

    # We will also simulate execution
    # To simulate execution, we need future prices.
    # We can fetch all future prices into memory for fast lookup.
    print("Loading OHLC cache for simulation...")
    sync_conn = sqlite3.connect("data/project_alpha.db")
    df_db = pd.read_sql_query(
        "SELECT pair, timestamp, open, high, low, close FROM market_candles WHERE timeframe='1h'",
        sync_conn,
    )
    sync_conn.close()

    cache = {}
    for pair, grp in df_db.groupby("pair"):
        grp = grp.sort_values("timestamp")
        cache[pair] = {
            "ts": grp["timestamp"].values,
            "open": grp["open"].values,
            "high": grp["high"].values,
            "low": grp["low"].values,
        }

    active_trades = []
    completed_trades = []
    initial_capital = 100000.0
    current_equity = initial_capital

    from background.backtest.friction import CoinDCXFrictionModel, FrictionConfig

    friction_model = CoinDCXFrictionModel(FrictionConfig())

    for i in range(start_idx, len(timestamps)):
        current_ts = timestamps[i]

        # 1. Evaluate Active Trades (Exits at Open)
        for trade in active_trades[:]:
            pair = trade["pair"]
            p_cache = cache.get(pair)
            if not p_cache:
                continue

            # Find the bar for current_ts
            idx = np.searchsorted(p_cache["ts"], current_ts)
            if idx < len(p_cache["ts"]) and p_cache["ts"][idx] == current_ts:
                # We are at current_ts Open
                o = p_cache["open"][idx]
                h = p_cache["high"][idx]
                l = p_cache["low"][idx]

                # Did we gap past SL/TP?
                # Actually, check high/low
                sl = trade["sl"]
                tp = trade["tp"]

                exit_price = None
                exit_reason = ""

                if l <= sl:
                    exit_price = sl  # Assume we get filled at SL
                    exit_reason = "STOP_LOSS"
                elif h >= tp:
                    exit_price = tp
                    exit_reason = "TAKE_PROFIT"

                if exit_price:
                    qty = trade["qty"]
                    pnl_data = friction_model.calculate_trade_net_pnl(
                        trade["entry"], exit_price, qty
                    )
                    current_equity += pnl_data["net_pnl"]
                    trade["exit"] = exit_price
                    trade["pnl"] = pnl_data["net_pnl"]
                    trade["exit_reason"] = exit_reason
                    completed_trades.append(trade)
                    active_trades.remove(trade)

        # 2. Run Scanner for new entries
        mc_svc.set_time(current_ts)
        # Unlock poll if locked
        if scanner._poll_lock.locked():
            scanner._poll_lock.release()

        await scanner.poll(max_timestamp_ms=current_ts)

        # Any new signals?
        while generated_signals:
            sig = generated_signals.pop(0)
            pair = sig["pair"]

            # Don't enter if already active
            if any(t["pair"] == pair for t in active_trades):
                continue

            # Entry executes at Next Bar Open, so we just add it to pending or execute at current Close?
            # Wait, signal generated AT current_ts (using candles UP TO current_ts).
            # We can execute at the Close of current_ts, or Open of current_ts + 1h.
            # Let's execute at current_ts Close (which is same as next bar Open roughly).

            p_cache = cache.get(pair) or cache.get(
                pair.replace("B-", "").replace("_", "/")
            )
            if p_cache:
                idx = np.searchsorted(p_cache["ts"], current_ts)
                if idx + 1 < len(p_cache["ts"]):
                    entry_price = p_cache["open"][idx + 1]

                    sl = sig.get("stop_loss", entry_price * 0.95)
                    tp = sig.get("take_profit", entry_price * 1.05)

                    # Risk size 1%
                    risk_amt = current_equity * 0.01
                    risk_per_coin = entry_price - sl
                    if risk_per_coin > 0:
                        qty = risk_amt / risk_per_coin

                        active_trades.append(
                            {
                                "pair": pair,
                                "entry": entry_price,
                                "sl": sl,
                                "tp": tp,
                                "qty": qty,
                                "time": current_ts,
                                "score": sig.get("score", 0),
                                "regime": mc_svc._latest_context.get(
                                    "market_regime", "UNKNOWN"
                                ),
                            }
                        )

        if i % 100 == 0:
            print(
                f"Progress: {i}/{len(timestamps)} | Equity: {current_equity:.2f} | Completed: {len(completed_trades)} | Active: {len(active_trades)}"
            )

    print("\n\n" + "=" * 80)
    print(" PROJECT-ALPHA V2 — PHASE G: HISTORICAL BACKTEST REPORT")
    print("=" * 80)
    print(f"Initial Equity: INR {initial_capital:,.2f}")
    print(f"Final Equity  : INR {current_equity:,.2f}")

    total_pnl = current_equity - initial_capital
    print(
        f"Net Realized PnL: INR {total_pnl:,.2f} ({(total_pnl / initial_capital) * 100:.2f}%)"
    )
    print(f"Total Trades  : {len(completed_trades)}")

    if not completed_trades:
        print("\nNo trades were generated. Backtest complete.")
        return

    wins = [t for t in completed_trades if t["pnl"] > 0]
    losses = [t for t in completed_trades if t["pnl"] <= 0]

    win_rate = (len(wins) / len(completed_trades)) * 100
    print(f"Win Rate      : {win_rate:.2f}%")

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    print(f"Gross Profit Factor: {profit_factor:.2f}")

    expectancy = total_pnl / len(completed_trades)
    print(f"Expectancy    : INR {expectancy:,.2f} per trade")

    # Calculate Max Drawdown
    peak = initial_capital
    max_dd = 0.0
    eq = initial_capital
    for t in completed_trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)
    print(f"Max Drawdown  : {max_dd:.2f}%")

    print("\n--- REGIME BREAKDOWN ---")
    regime_pnl = {}
    for t in completed_trades:
        reg = t.get("regime", "UNKNOWN")
        regime_pnl[reg] = regime_pnl.get(reg, 0) + t["pnl"]
    for reg, p in regime_pnl.items():
        print(f"  {reg}: INR {p:,.2f}")

    print("\n--- SCORE BREAKDOWN ---")
    score_pnl = {"Elite (90+)": 0, "High (80-89)": 0, "Medium (<80)": 0}
    for t in completed_trades:
        s = t.get("score", 0)
        if s >= 90:
            score_pnl["Elite (90+)"] += t["pnl"]
        elif s >= 80:
            score_pnl["High (80-89)"] += t["pnl"]
        else:
            score_pnl["Medium (<80)"] += t["pnl"]
    for cat, p in score_pnl.items():
        print(f"  {cat}: INR {p:,.2f}")

    print("\n--- COIN BREAKDOWN ---")
    coin_pnl = {}
    for t in completed_trades:
        c = t["pair"]
        coin_pnl[c] = coin_pnl.get(c, 0) + t["pnl"]
    for c, p in sorted(coin_pnl.items(), key=lambda x: x[1], reverse=True):
        print(f"  {c}: INR {p:,.2f}")

    print("=" * 80)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
