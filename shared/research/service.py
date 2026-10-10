"""
V2 CoinResearchService — On-Demand Coin Research & Intelligence.

Strictly read-only: never publishes events, never writes production tables.
Isolation boundary: reads from market_candles + CoinDCX public APIs only.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from background.backtest.engine import BacktestEngine
from background.backtest.strategies import (
    BBSStrategy,
    HDAStrategy,
    MRBStrategy,
    MTBStrategy,
    NR7Strategy,
    PPAStrategy,
    STEStrategy,
    VCPStrategy,
)
from core.config import AppConfig, get_config
from core.logging import get_logger
from core.repository.candle_repo import CandleRepository
try:
    from shared.market.public_client import CoinDCXPublicClient
except ImportError:
    try:
        from market.public_client import CoinDCXPublicClient
    except ImportError:
        from scanner.market.public_client import CoinDCXPublicClient

from .indicators import (
    compute_atr,
    compute_bollinger,
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_rvol,
    last_valid,
)
from .symbol_normalizer import (
    is_supported_pair,
    normalize_symbol,
    resolve_tradeable_pairs,
)

logger = get_logger("scanner.research")

# Strategy name → class map
_STRATEGY_MAP = {
    "VCP": VCPStrategy,
    "STE": STEStrategy,
    "HDA": HDAStrategy,
    "BBS": BBSStrategy,
    "NR7": NR7Strategy,
    "PPA": PPAStrategy,
    "MTB": MTBStrategy,
    "MRB": MRBStrategy,
}

TIMEFRAMES = [
    ("1m", "1m", 60),
    ("15m", "15m", 900),
    ("15m", "15m", 3600),
    ("1d", "1d", 86400),
]


class CoinResearchService:
    """
    On-demand analytics engine for the Research Hub.

    Capabilities:
    - resolve_coin_pairs(): discovers tradeable pairs for base asset (INR priority, USDT fallback)
    - get_ticker_snapshot(): sub-50ms live ticker snapshot for fast polling
    - fetch_full_coin_profile(): live multi-TF indicators + VCP + scorecard
    - run_on_demand_backtest(): historical backtest with statutory friction
    - predict_trend_and_catalysts(): rule-based multi-horizon forecast

    Does NOT: publish events, write production tables, place orders.
    """

    def __init__(
        self,
        public_client: CoinDCXPublicClient | None = None,
        candle_repo: CandleRepository | None = None,
        config: AppConfig | None = None,
    ) -> None:
        self._candle_repo = candle_repo or CandleRepository()
        self._config = config or get_config()
        self._public_client = public_client or CoinDCXPublicClient(
            timeout=10.0, rate_limit_per_sec=6.0
        )

    # ── Public Interface ──────────────────────────────────────────────────────

    async def resolve_coin_pairs(self, base_asset: str) -> dict[str, Any]:
        """
        Discovers all tradeable pairs for a given base asset.
        Prioritizes active INR pairs with seamless fallback to USDT.
        """
        resolved = resolve_tradeable_pairs(base_asset)
        base = resolved["base_asset"]

        # Query live USDT/INR rate from ticker
        usdt_inr_rate = 91.50
        try:
            usdt_ticker = await self._fetch_ticker("USDT/INR")
            if usdt_ticker.get("ltp") and usdt_ticker["ltp"] > 0:
                usdt_inr_rate = float(usdt_ticker["ltp"])
        except Exception:
            pass

        # Check if INR pair actually exists on live CoinDCX exchange
        try:
            inr_ticker = await self._fetch_ticker(f"{base}/INR")
            inr_available = bool(inr_ticker.get("ltp") and inr_ticker["ltp"] > 0)
        except Exception:
            inr_available = False

        if not inr_available and resolved["has_usdt"]:
            primary_pair = f"{base}/USDT"
            preferred_quote = "USDT"
        else:
            primary_pair = resolved["primary_pair"]
            preferred_quote = resolved["preferred_quote"]

        return {
            "base_asset": base,
            "primary_pair": primary_pair,
            "available_pairs": resolved["available_pairs"],
            "preferred_quote": preferred_quote,
            "usdt_inr_rate": usdt_inr_rate,
            "has_inr": resolved["has_inr"],
            "has_usdt": resolved["has_usdt"],
        }

    async def get_ticker_snapshot(self, symbol: str) -> dict[str, Any]:
        """
        Sub-50ms lightweight ticker snapshot for high-frequency 1-second UI updates.
        """
        pair = normalize_symbol(symbol)
        quote = pair.split("/")[1] if "/" in pair else "INR"
        base = pair.split("/")[0] if "/" in pair else pair

        t = await self._fetch_ticker(pair)

        # Calculate INR equivalent if quote is USDT
        usdt_rate = 91.50
        if quote == "USDT":
            try:
                u_t = await self._fetch_ticker("USDT/INR")
                if u_t.get("ltp") and u_t["ltp"] > 0:
                    usdt_rate = float(u_t["ltp"])
            except Exception:
                pass

        ltp = float(t.get("ltp") or 0.0)
        price_inr_equiv = (ltp * usdt_rate) if quote == "USDT" else ltp
        usdt_equiv = (
            ltp if quote == "USDT" else (ltp / usdt_rate if usdt_rate > 0 else ltp)
        )

        return {
            "symbol": base,
            "pair": pair,
            "quote": quote,
            "quote_currency": quote,
            "price": ltp,
            "ltp": ltp,
            "price_inr_equiv": round(price_inr_equiv, 2),
            "inr_equivalent_ltp": round(price_inr_equiv, 2),
            "usdt_equivalent_ltp": round(usdt_equiv, 4),
            "usdt_inr_rate": round(usdt_rate, 2),
            "change_24h": float(t.get("change_24h_pct") or 0.0),
            "change_24h_pct": float(t.get("change_24h_pct") or 0.0),
            "high_24h": float(t.get("high_24h") or 0.0),
            "low_24h": float(t.get("low_24h") or 0.0),
            "volume_24h": float(t.get("volume_24h") or 0.0),
            "bid": float(t.get("bid") or 0.0),
            "ask": float(t.get("ask") or 0.0),
            "timestamp": int(time.time()),
        }

    async def fetch_full_coin_profile(self, symbol: str) -> dict[str, Any]:
        """
        Fetch live market data + compute full technical profile for a coin.

        Returns:
            dict with keys: pair, ticker, indicators, vcp_setup, scorecard
        Raises:
            ValueError: if symbol is not supported
        """
        pair = normalize_symbol(symbol)
        if not is_supported_pair(pair):
            raise ValueError(
                f"Unsupported pair: '{pair}'. Check /api/research/coins for valid options."
            )

        logger.info("Fetching coin profile", extra={"pair": pair})

        # 1. Fetch ticker + candles concurrently
        ticker_task = asyncio.create_task(self._fetch_ticker(pair))
        candles_15m_task = asyncio.create_task(self._get_candles(pair, "15m", 120))
        candles_1h_task = asyncio.create_task(self._get_candles(pair, "1h", 120))
        candles_1d_task = asyncio.create_task(self._get_candles(pair, "1d", 365))

        ticker = await ticker_task
        c_15m = await candles_15m_task
        c_1h = await candles_1h_task
        c_1d = await candles_1d_task

        # 2. Compute indicators on each timeframe
        ind_15m = self._compute_indicators(c_15m, "15m")
        ind_1h = self._compute_indicators(c_1h, "1h")
        ind_1d = self._compute_indicators(c_1d, "1d")

        # 52-week high/low from 1d candles
        week52 = self._compute_52w_range(c_1d)

        # 3. VCP detection from 1d candles (needs at least 30 bars)
        vcp_setup = self._detect_vcp(c_1d, ticker)

        # 4. BTC reference for relative strength
        btc_rs_score = await self._compute_relative_strength(pair, c_1d)

        # 5. 100-point 4-pillar scorecard
        scorecard = self._compute_scorecard(
            ind_1d=ind_1d,
            ind_15m=ind_15m,
            ticker=ticker,
            vcp_setup=vcp_setup,
            btc_rs_score=btc_rs_score,
            candles_1d=c_1d,
        )

        return {
            "pair": pair,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "week52": week52,
            "indicators": {
                "15m": ind_15m,
                "1h": ind_1h,
                "1d": ind_1d,
            },
            "vcp_setup": vcp_setup,
            "scorecard": scorecard,
        }

    async def run_on_demand_backtest(
        self,
        symbol: str,
        strategy: str = "STE",
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Run an on-demand single-coin historical backtest.

        Applies zero look-ahead bias and 1.572% round-trip statutory friction.
        Returns serializable PerformanceMetrics dict.
        """
        pair = normalize_symbol(symbol)
        if not is_supported_pair(pair):
            raise ValueError(f"Unsupported pair: '{pair}'.")

        strat_key = strategy.upper()
        if strat_key not in _STRATEGY_MAP:
            raise ValueError(
                f"Unknown strategy '{strategy}'. "
                f"Valid: {', '.join(sorted(_STRATEGY_MAP.keys()))}"
            )

        strategy_instance = _STRATEGY_MAP[strat_key]()

        logger.info(
            "Running on-demand backtest",
            extra={"pair": pair, "strategy": strat_key, "days": days},
        )

        start_time = int(time.time()) - (days * 86400)

        engine = BacktestEngine(initial_capital=10_000.0)

        # Run backtest using real DB candles where available, then fallback
        db_path = self._config.db_path

        loop = asyncio.get_running_loop()
        metrics = await loop.run_in_executor(
            None,
            lambda: engine.run_historical_backtest(
                strategy=strategy_instance,
                pairs=[pair],
                timeframes=["15M", "1H"],
                db_path=db_path,
                start_time=start_time,
            ),
        )

        return {
            "pair": pair,
            "strategy": strat_key,
            "days": days,
            "total_trades": metrics.total_trades,
            "winning_trades": metrics.winning_trades,
            "losing_trades": metrics.losing_trades,
            "win_rate_pct": metrics.win_rate_pct,
            "net_pnl_pct": metrics.net_pnl_pct,
            "net_realized_pnl_inr": round(metrics.net_realized_pnl_dollars, 2),
            "gross_profit_factor": metrics.gross_profit_factor,
            "net_profit_factor": metrics.net_profit_factor,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "avg_net_rr": metrics.avg_net_rr,
            "expectancy_per_trade": metrics.expectancy_per_trade,
            "survives_friction": metrics.survives_friction,
            "statutory_drag_pct": 1.572,
            "initial_capital": 10_000.0,
            "ran_at": datetime.now(timezone.utc).isoformat(),
        }

    async def predict_trend_and_catalysts(
        self,
        symbol: str,
        indicators: dict | None = None,
    ) -> dict[str, Any]:
        """
        Generate rule-based multi-horizon trend prediction.

        Analyzes momentum, EMA stack, RSI, MACD, and Bollinger width to
        generate 1h, 4h, and 24h directional forecasts with confidence scores.
        Does NOT place orders or modify any state.
        """
        pair = normalize_symbol(symbol)
        if not is_supported_pair(pair):
            raise ValueError(f"Unsupported pair: '{pair}'.")

        # If indicators not provided, fetch them fresh
        if indicators is None:
            candles_15m = await self._get_candles(pair, "15m", 120)
            candles_1d = await self._get_candles(pair, "1d", 60)
            ind_15m = self._compute_indicators(candles_15m, "15m")
            ind_1d = self._compute_indicators(candles_1d, "1d")
        else:
            ind_15m = indicators.get("15m", {})
            ind_1d = indicators.get("1d", {})

        prediction = self._generate_prediction(pair, ind_15m, ind_1d)
        return prediction

    # ── Internal: Candle Fetching ─────────────────────────────────────────────

    async def _fetch_ticker(self, pair: str) -> dict[str, Any]:
        """Fetch live ticker for the pair from CoinDCX."""
        try:
            tickers = await self._public_client.get_tickers()

            # Map pair to wire market name variants: "BTC/INR" → "BTCINR", "B-BTC_INR", "BTC_INR"
            base, quote = pair.split("/")
            base_u = base.upper()
            quote_u = quote.upper()
            target_direct = f"{base_u}{quote_u}"
            target_coindcx = f"B-{base_u}_{quote_u}"
            target_usdt = f"{base_u}USDT"

            for t in tickers:
                mkt = str(t.get("market", "")).upper()
                mkt_clean = mkt.replace("-", "").replace("_", "")
                if (
                    mkt == target_direct
                    or mkt == target_coindcx
                    or mkt_clean == target_direct
                    or mkt_clean == f"B{target_direct}"
                    or mkt_clean == f"I{target_direct}"
                    or (quote_u == "USDT" and mkt_clean == target_usdt)
                ):
                    return {
                        "ltp": float(t.get("last_price") or 0.0),
                        "change_24h_pct": float(t.get("change_24_hour") or 0.0),
                        "high_24h": float(t.get("high") or 0.0),
                        "low_24h": float(t.get("low") or 0.0),
                        "volume_24h": float(t.get("volume") or 0.0),
                        "bid": float(t.get("bid") or 0.0),
                        "ask": float(t.get("ask") or 0.0),
                    }
        except Exception as exc:
            logger.warning(
                "Ticker fetch failed", extra={"pair": pair, "error": str(exc)}
            )

        return {
            "ltp": 0.0,
            "change_24h_pct": 0.0,
            "high_24h": 0.0,
            "low_24h": 0.0,
            "volume_24h": 0.0,
            "bid": 0.0,
            "ask": 0.0,
        }

    async def _get_candles(self, pair: str, timeframe: str, limit: int) -> list[dict]:
        """
        Fetch candles: check local DB first, fall back to CoinDCX API.
        """
        try:
            cached = await self._candle_repo.get_recent_candles(pair, timeframe, limit)
            if len(cached) >= 30:
                return cached
        except Exception:
            pass  # DB not available in all test contexts

        # Fetch from CoinDCX public API
        try:
            raw = await self._public_client.get_candles(
                pair, interval=timeframe, limit=limit
            )
            return raw
        except Exception as exc:
            logger.warning(
                "Candle fetch failed",
                extra={"pair": pair, "timeframe": timeframe, "error": str(exc)},
            )
            return []

    # ── Internal: Indicator Computation ──────────────────────────────────────

    def _compute_indicators(
        self, candles: list[dict], timeframe: str
    ) -> dict[str, Any]:
        """Compute all technical indicators for a candle list."""
        if len(candles) < 5:
            return {"status": "INSUFFICIENT_DATA", "bars": len(candles)}

        closes = np.array([float(c["close"]) for c in candles])
        highs = np.array([float(c["high"]) for c in candles])
        lows = np.array([float(c["low"]) for c in candles])
        vols = np.array([float(c["volume"]) for c in candles])

        ema9 = compute_ema(closes, 9)
        ema21 = compute_ema(closes, 21)
        ema50 = compute_ema(closes, 50)
        ema200 = compute_ema(closes, 200)
        rsi14 = compute_rsi(closes, 14)
        macd_l, macd_s, macd_h = compute_macd(closes)
        bb_up, bb_mid, bb_lo = compute_bollinger(closes, 20, 2.0)
        atr14 = compute_atr(highs, lows, closes, 14)
        rvol = compute_rvol(vols, 20)

        price = float(closes[-1])
        atr_v = last_valid(atr14)

        # EMA stack analysis
        e9 = last_valid(ema9)
        e21 = last_valid(ema21)
        e50 = last_valid(ema50)
        e200 = last_valid(ema200)

        trend_aligned = (
            e50 > 0.0
            and price > e21 > e50
            and e9 > e21
            and (e200 == 0.0 or e50 > e200)  # 200 may not have enough data
        )

        return {
            "status": "OK",
            "bars": len(candles),
            "timeframe": timeframe,
            "close": round(price, 4),
            "ema9": round(e9, 4),
            "ema21": round(e21, 4),
            "ema50": round(e50, 4),
            "ema200": round(e200, 4),
            "rsi14": round(last_valid(rsi14), 2),
            "macd": round(last_valid(macd_l), 4),
            "macd_signal": round(last_valid(macd_s), 4),
            "macd_hist": round(last_valid(macd_h), 4),
            "bb_upper": round(last_valid(bb_up), 4),
            "bb_mid": round(last_valid(bb_mid), 4),
            "bb_lower": round(last_valid(bb_lo), 4),
            "bb_width_pct": round(
                (
                    (last_valid(bb_up) - last_valid(bb_lo)) / last_valid(bb_mid) * 100
                    if last_valid(bb_mid) > 0
                    else 0.0
                ),
                2,
            ),
            "atr14": round(atr_v, 4),
            "atr_pct": round(atr_v / price * 100 if price > 0 else 0.0, 2),
            "rvol": rvol,
            "trend_aligned": trend_aligned,
        }

    def _compute_52w_range(self, candles_1d: list[dict]) -> dict[str, Any]:
        """Compute 52-week high/low from daily candles (up to 365 bars)."""
        if not candles_1d:
            return {"high_52w": None, "low_52w": None, "pct_from_52w_high": None}

        highs = [float(c["high"]) for c in candles_1d]
        lows = [float(c["low"]) for c in candles_1d]
        close = float(candles_1d[-1]["close"])

        high_52w = max(highs)
        low_52w = min(lows)
        pct_from_high = (
            round((close - high_52w) / high_52w * 100, 2) if high_52w > 0 else 0.0
        )

        return {
            "high_52w": round(high_52w, 4),
            "low_52w": round(low_52w, 4),
            "pct_from_52w_high": pct_from_high,
        }

    # ── Internal: VCP Detection ───────────────────────────────────────────────

    def _detect_vcp(self, candles_1d: list[dict], ticker: dict) -> dict[str, Any]:
        """
        Detect Minervini Volatility Contraction Pattern (VCP).

        Identifies up to 3 contraction stages (T1, T2, T3) and computes:
        - Pivot buy point
        - Hard stop-loss (below T3 low)
        - Target 1 (1:2 R)
        - Target 2 (1:3.5 R)
        """
        default = {
            "detected": False,
            "stages": [],
            "pivot_buy_point": None,
            "hard_stop_loss": None,
            "target_1": None,
            "target_2": None,
            "contraction_count": 0,
            "setup_quality": "NO_SETUP",
        }

        if len(candles_1d) < 30:
            return default

        closes = np.array([float(c["close"]) for c in candles_1d])
        highs = np.array([float(c["high"]) for c in candles_1d])
        lows = np.array([float(c["low"]) for c in candles_1d])
        # Find swings over last 60 bars (or all if < 60)
        window = min(60, len(candles_1d))
        h_slice = highs[-window:]
        l_slice = lows[-window:]

        stages = []
        swing_highs = []
        swing_lows = []

        # Detect local swing highs/lows (simple 3-bar pivot)
        for i in range(2, len(h_slice) - 2):
            if (
                h_slice[i] > h_slice[i - 1]
                and h_slice[i] > h_slice[i - 2]
                and h_slice[i] > h_slice[i + 1]
                and h_slice[i] > h_slice[i + 2]
            ):
                swing_highs.append((i, float(h_slice[i])))
            if (
                l_slice[i] < l_slice[i - 1]
                and l_slice[i] < l_slice[i - 2]
                and l_slice[i] < l_slice[i + 1]
                and l_slice[i] < l_slice[i + 2]
            ):
                swing_lows.append((i, float(l_slice[i])))

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return default

        # Build contraction stages between consecutive swing pairs
        for k in range(1, min(4, len(swing_highs))):
            prev_h = (
                swing_highs[-k - 1][1]
                if (k + 1) <= len(swing_highs)
                else swing_highs[0][1]
            )
            curr_h = swing_highs[-k][1]

            prev_l_idx = (
                min(
                    range(len(swing_lows)),
                    key=lambda j: abs(swing_lows[j][0] - swing_highs[-k - 1][0]),
                )
                if len(swing_lows) > 1
                else 0
            )
            curr_l_idx = min(
                range(len(swing_lows)),
                key=lambda j: abs(swing_lows[j][0] - swing_highs[-k][0]),
            )
            prev_l = swing_lows[prev_l_idx][1]
            curr_l = swing_lows[curr_l_idx][1]

            range_prev = prev_h - prev_l
            range_curr = curr_h - curr_l

            if range_prev > 0 and range_curr < range_prev:
                contraction_pct = round((1 - range_curr / range_prev) * 100, 1)
                stages.append(
                    {
                        "stage": f"T{k}",
                        "high": round(curr_h, 4),
                        "low": round(curr_l, 4),
                        "range": round(range_curr, 4),
                        "contraction_pct": contraction_pct,
                    }
                )

        if not stages:
            return default

        stages = list(reversed(stages))  # T1 is earliest

        # Pivot buy point = latest swing high + small buffer
        latest_high = swing_highs[-1][1]
        pivot_bp = round(latest_high * 1.002, 4)

        # Hard SL = last swing low − ATR buffer
        latest_low = swing_lows[-1][1]
        atr_arr = compute_atr(highs[-30:], lows[-30:], closes[-30:], 14)
        atr_val = last_valid(atr_arr)
        hard_sl = round(max(latest_low - atr_val, latest_low * 0.97), 4)

        risk = pivot_bp - hard_sl
        target1 = round(pivot_bp + risk * 2.0, 4)
        target2 = round(pivot_bp + risk * 3.5, 4)

        quality = (
            "EXCELLENT"
            if len(stages) >= 3
            else "GOOD"
            if len(stages) == 2
            else "DEVELOPING"
        )

        return {
            "detected": len(stages) >= 1,
            "stages": stages,
            "pivot_buy_point": pivot_bp,
            "hard_stop_loss": hard_sl,
            "target_1": target1,
            "target_2": target2,
            "contraction_count": len(stages),
            "setup_quality": quality,
        }

    # ── Internal: Relative Strength ───────────────────────────────────────────

    async def _compute_relative_strength(
        self, pair: str, candles_1d: list[dict]
    ) -> float:
        """
        Compute relative strength vs BTC over last 15 days.
        Returns a score 0-25 (for scorecard pillar).
        """
        if pair in ("BTC/INR", "BTC/USDT") or not candles_1d or len(candles_1d) < 16:
            return 12.5  # Neutral for BTC itself or insufficient data

        try:
            btc_candles = await self._get_candles("BTC/INR", "1d", 20)
            if len(btc_candles) < 16 or len(candles_1d) < 16:
                return 12.5

            coin_close_start = float(candles_1d[-16]["close"])
            coin_close_end = float(candles_1d[-1]["close"])
            btc_close_start = float(btc_candles[-16]["close"])
            btc_close_end = float(btc_candles[-1]["close"])

            if coin_close_start <= 0 or btc_close_start <= 0:
                return 12.5

            coin_return = (coin_close_end - coin_close_start) / coin_close_start
            btc_return = (btc_close_end - btc_close_start) / btc_close_start

            rs = coin_return - btc_return

            # Map RS to 0-25 score
            if rs >= 0.10:
                return 25.0
            elif rs >= 0.05:
                return 20.0
            elif rs >= 0.02:
                return 17.0
            elif rs >= 0.0:
                return 14.0
            elif rs >= -0.05:
                return 10.0
            else:
                return 5.0
        except Exception:
            return 12.5

    # ── Internal: 100-Point Scorecard ─────────────────────────────────────────

    def _compute_scorecard(
        self,
        ind_1d: dict,
        ind_15m: dict,
        ticker: dict,
        vcp_setup: dict,
        btc_rs_score: float,
        candles_1d: list[dict],
    ) -> dict[str, Any]:
        """
        Compute 4-pillar 100-point Quality Index.

        Pillar 1 — Technical Structure (25 pts)
        Pillar 2 — Relative Strength vs BTC (25 pts)
        Pillar 3 — Volume Delivery (25 pts)
        Pillar 4 — Risk/Reward Asymmetry (25 pts)
        """

        # ── Pillar 1: Technical Structure ───────────────────────────────────
        p1 = 0.0
        if ind_1d.get("status") == "OK":
            # EMA stack: price > EMA21 > EMA50
            close = ind_1d.get("close", 0.0)
            e21 = ind_1d.get("ema21", 0.0)
            e50 = ind_1d.get("ema50", 0.0)
            e200 = ind_1d.get("ema200", 0.0)
            rsi = ind_1d.get("rsi14", 50.0)
            macd_h = ind_1d.get("macd_hist", 0.0)

            if close > e21 > e50:
                p1 += 10
            elif close > e21:
                p1 += 6
            if e200 > 0 and e50 > e200:
                p1 += 5
            if 40 <= rsi <= 70:
                p1 += 5
            elif rsi > 70:
                p1 += 2  # Overbought
            if macd_h > 0:
                p1 += 5

        # ── Pillar 2: Relative Strength ─────────────────────────────────────
        p2 = round(btc_rs_score, 1)

        # ── Pillar 3: Volume Delivery ────────────────────────────────────────
        p3 = 0.0
        rvol = ind_1d.get("rvol", 1.0) if ind_1d.get("status") == "OK" else 1.0
        if rvol >= 2.0:
            p3 = 25.0
        elif rvol >= 1.5:
            p3 = 20.0
        elif rvol >= 1.2:
            p3 = 16.0
        elif rvol >= 1.0:
            p3 = 12.0
        elif rvol >= 0.8:
            p3 = 8.0
        else:
            p3 = 4.0

        # VCP volume contraction bonus
        if vcp_setup.get("detected"):
            p3 = min(25.0, p3 + 3.0)

        # ── Pillar 4: Risk/Reward Asymmetry ─────────────────────────────────
        p4 = 0.0
        if vcp_setup.get("detected"):
            pivot = vcp_setup.get("pivot_buy_point") or 0.0
            sl = vcp_setup.get("hard_stop_loss") or 0.0
            t1 = vcp_setup.get("target_1") or 0.0
            if pivot > 0 and sl > 0 and t1 > 0 and pivot > sl:
                rr = (t1 - pivot) / (pivot - sl)
                if rr >= 3.5:
                    p4 = 25.0
                elif rr >= 2.5:
                    p4 = 20.0
                elif rr >= 2.0:
                    p4 = 16.0
                elif rr >= 1.5:
                    p4 = 12.0
                else:
                    p4 = 6.0
        else:
            # Use ATR-based R:R estimate from daily
            atr_pct = (
                ind_1d.get("atr_pct", 0.0) if ind_1d.get("status") == "OK" else 0.0
            )
            if atr_pct > 0:
                p4 = min(15.0, round(atr_pct * 2, 1))  # Rough proxy

        total = round(p1 + p2 + p3 + p4, 1)

        return {
            "total_score": min(100.0, total),
            "pillar_technical_structure": round(p1, 1),
            "pillar_relative_strength": round(p2, 1),
            "pillar_volume_delivery": round(p3, 1),
            "pillar_risk_reward": round(p4, 1),
            "rating": (
                "STRONG BUY"
                if total >= 80
                else (
                    "BUY"
                    if total >= 65
                    else (
                        "WATCH"
                        if total >= 50
                        else "NEUTRAL"
                        if total >= 35
                        else "AVOID"
                    )
                )
            ),
        }

    # ── Internal: Rule-Based Prediction ──────────────────────────────────────

    def _generate_prediction(
        self,
        pair: str,
        ind_15m: dict,
        ind_1d: dict,
    ) -> dict[str, Any]:
        """
        Generate multi-horizon trend forecast using indicator momentum rules.

        Horizons: 1h, 4h, 24h.
        Directions: BULLISH / BEARISH / CONSOLIDATION.
        """

        def _score_direction(ind: dict) -> tuple[str, int, list[str], list[str]]:
            """Returns (direction, confidence_pct, catalysts, risks)."""
            if not ind or ind.get("status") != "OK":
                return "CONSOLIDATION", 40, [], ["Insufficient data"]

            bullish_pts = 0
            bearish_pts = 0
            catalysts = []
            risks = []

            rsi = ind.get("rsi14", 50.0)
            close = ind.get("close", 0.0)
            e9 = ind.get("ema9", 0.0)
            e21 = ind.get("ema21", 0.0)
            e50 = ind.get("ema50", 0.0)
            macd_h = ind.get("macd_hist", 0.0)
            rvol = ind.get("rvol", 1.0)
            bb_lo = ind.get("bb_lower", 0.0)
            bb_up = ind.get("bb_upper", 0.0)
            bw_pct = ind.get("bb_width_pct", 5.0)

            # RSI analysis
            if rsi < 30:
                bullish_pts += 2
                catalysts.append(
                    f"RSI oversold ({rsi:.1f}) — reversal probability high"
                )
            elif rsi < 45:
                bullish_pts += 1
                catalysts.append(f"RSI approaching oversold territory ({rsi:.1f})")
            elif rsi > 70:
                bearish_pts += 2
                risks.append(f"RSI overbought ({rsi:.1f}) — pullback risk elevated")
            elif rsi > 55:
                bullish_pts += 1
                catalysts.append(f"RSI in bullish momentum zone ({rsi:.1f})")

            # EMA positioning
            if e50 > 0.0 and close > e21 > e50:
                bullish_pts += 3
                catalysts.append(
                    "Price above EMA21 and EMA50 — bullish structure intact"
                )
            elif close > e21:
                bullish_pts += 1
                catalysts.append("Price above EMA21")
            elif e50 > 0.0 and close < e21 < e50:
                bearish_pts += 3
                risks.append("Price below both EMA21 and EMA50 — bearish structure")
            elif close < e21:
                bearish_pts += 1
                risks.append("Price below EMA21 — caution")

            # MACD histogram
            if macd_h > 0:
                bullish_pts += 2
                catalysts.append("MACD histogram positive — bullish momentum")
            elif macd_h < 0:
                bearish_pts += 2
                risks.append("MACD histogram negative — bearish momentum")

            # Volume
            if rvol >= 1.5:
                if close >= e9:
                    bullish_pts += 1
                    catalysts.append(
                        f"Bullish volume surge (RVOL {rvol:.2f}x) confirming move"
                    )
                else:
                    bearish_pts += 1
                    risks.append(
                        f"Bearish volume surge (RVOL {rvol:.2f}x) indicating distribution"
                    )
            elif rvol < 0.7:
                risks.append(
                    f"Below-average volume (RVOL {rvol:.2f}x) — weak conviction"
                )

            # Bollinger position
            if close > 0 and bb_lo > 0 and close < bb_lo:
                bullish_pts += 1
                catalysts.append(
                    "Price below lower Bollinger Band — mean reversion potential"
                )
            elif close > 0 and bb_up > 0 and close > bb_up:
                bearish_pts += 1
                risks.append("Price above upper Bollinger Band — overextension")
            if bw_pct < 3.0:
                catalysts.append(
                    f"Bollinger squeeze ({bw_pct:.1f}%) — volatility expansion imminent"
                )

            # Determine direction
            net = bullish_pts - bearish_pts
            if net >= 4:
                direction = "BULLISH"
                confidence = min(90, 55 + net * 7)
            elif net >= 2:
                direction = "BULLISH"
                confidence = min(75, 50 + net * 5)
            elif net <= -4:
                direction = "BEARISH"
                confidence = min(90, 55 + abs(net) * 7)
            elif net <= -2:
                direction = "BEARISH"
                confidence = min(75, 50 + abs(net) * 5)
            else:
                direction = "CONSOLIDATION"
                confidence = 45

            return direction, confidence, catalysts, risks

        # Compute 1h and 24h forecasts
        dir_15m, conf_15m, cat_15m, risk_15m = _score_direction(ind_15m)
        dir_24h, conf_24h, cat_24h, risk_24h = _score_direction(ind_1d)

        # 4h is interpolated between 1h and 24h
        dir_1h = dir_15m if dir_15m == dir_24h else "CONSOLIDATION"
        conf_1h = round((conf_15m + conf_24h) / 2)

        # Key levels
        e21 = ind_15m.get("ema21", 0.0) if ind_15m.get("status") == "OK" else 0.0
        bb_lo = ind_15m.get("bb_lower", 0.0) if ind_15m.get("status") == "OK" else 0.0
        bb_up = ind_15m.get("bb_upper", 0.0) if ind_15m.get("status") == "OK" else 0.0

        support_levels = sorted({l for l in [round(bb_lo, 4), round(e21, 4)] if l > 0})
        resistance_levels = sorted({l for l in [round(bb_up, 4)] if l > 0})

        # Combine catalysts (deduplicated from both timeframes)
        all_catalysts = list(dict.fromkeys(cat_15m + cat_24h))[:5]
        all_risks = list(dict.fromkeys(risk_15m + risk_24h))[:5]

        return {
            "pair": pair,
            "predicted_at": datetime.now(timezone.utc).isoformat(),
            "method": "RULE_BASED",
            "horizons": {
                "15m": {
                    "direction": dir_15m,
                    "confidence": conf_15m,
                    "description": f"{dir_15m} bias on 1-hour timeframe ({conf_15m}% confidence)",
                },
                "1h": {
                    "direction": dir_1h,
                    "confidence": conf_1h,
                    "description": f"{dir_1h} bias on 4-hour timeframe ({conf_1h}% confidence)",
                },
                "24h": {
                    "direction": dir_24h,
                    "confidence": conf_24h,
                    "description": f"{dir_24h} bias on 24-hour timeframe ({conf_24h}% confidence)",
                },
            },
            "key_support_levels": support_levels,
            "key_resistance_levels": resistance_levels,
            "bullish_catalysts": all_catalysts,
            "risk_factors": all_risks,
            "summary": (
                f"{'Bullish' if dir_24h == 'BULLISH' else 'Bearish' if dir_24h == 'BEARISH' else 'Neutral'} "
                f"outlook for {pair} on 24h horizon with {conf_24h}% confidence."
            ),
        }
