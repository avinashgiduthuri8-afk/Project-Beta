"""Deterministic five-stage scanner for NSE/BSE cash equities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import asdict, replace
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping

from core.repository.beta_store import BetaStore
from core.repository.quote_repo import QuoteRepository
from market.provider import MarketDataProvider
from market.session import BSE_SESSION, NSE_SESSION
from market.symbols import StockSymbol
from scanner.validation import MarketDataValidator
from scanner.eligibility import EligibilityEngine
from scanner.models import RankingEvidence, ScannerCandidate
from scanner.ranking import CandidateRanker
from execution.indian_charges import IndianEquityChargeModel
from shared.research.indicators import enrich_candles_df
from shared.strategies.base import BaseStockStrategy
from shared.strategies.registry import get_active_strategies
from market.holidays import HolidayCalendar
from market.session import IST
from market.universe import UniverseInstrument


class IndianScannerService:
    """Owns market access, evaluates the 5 active strategies, and writes only qualified paper signals."""

    def __init__(
        self,
        provider: MarketDataProvider,
        store: BetaStore,
        symbols: Iterable[StockSymbol],
        *,
        clock: Callable[[], datetime] | None = None,
        max_quote_age: timedelta = timedelta(minutes=15),
        quote_repo: QuoteRepository | None = None,
        strategies: Mapping[str, BaseStockStrategy] | None = None,
        price_ceiling: Decimal = Decimal("250.0"),
        price_floor: Decimal = Decimal("0.0"),
        min_atr_pct: float = 1.5,
        news_service: Any | None = None,
        telegram_client: Any | None = None,
        ranker: CandidateRanker | None = None,
        eligibility_engine: EligibilityEngine | None = None,
        instruments: Mapping[str, UniverseInstrument] | None = None,
        holiday_calendar: HolidayCalendar | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.symbols = tuple(symbols)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_quote_age = max_quote_age
        self.quote_repo = quote_repo
        self.validator = MarketDataValidator()
        self.strategies = dict(strategies) if strategies is not None else get_active_strategies()
        self.price_ceiling = Decimal(str(price_ceiling))
        self.price_floor = Decimal(str(price_floor))
        self.min_atr_pct = float(min_atr_pct)
        self.news_service = news_service
        self.telegram_client = telegram_client
        self.ranker = ranker or CandidateRanker()
        self.charge_model = IndianEquityChargeModel()
        self.eligibility_engine = eligibility_engine
        self.instruments = dict(instruments or {})
        self.holiday_calendar = holiday_calendar

    def _affordable_quantity(self, symbol: StockSymbol, price: Decimal) -> int:
        quantity = int((Decimal("1000") / price).to_integral_value(rounding=ROUND_FLOOR))
        while quantity > 0:
            charges = self.charge_model.estimate("BUY", price, quantity, symbol.exchange)
            if price * quantity + charges.total <= Decimal("1000"):
                return quantity
            quantity -= 1
        return 0

    async def poll(self, max_timestamp_ms: int | None = None) -> dict:
        stages = [1, 2, 3, 4, 5, 6, 7, 8]
        result = {"stages": stages, "qualified": 0, "duplicates": 0, "rejections": []}
        now = self.clock()
        cycle_id = "cycle-" + sha256(now.isoformat().encode()).hexdigest()[:20]
        prepared: dict[str, tuple[ScannerCandidate, Any, Any]] = {}

        async def reject(symbol: StockSymbol, stage: int, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
            result["rejections"].append({"symbol": symbol.canonical, "stage": stage, "code": code})
            await self.store.record_scanner_decision(
                cycle_id, symbol.canonical, stage, code, message, evidence or {}, now.isoformat()
            )

        # Cheap evidence pass: every configured symbol is considered before top-50 truncation.
        for symbol in sorted(self.symbols, key=lambda item: item.canonical):
            # Stage 1: supported exchange and open cash-market session.
            session = NSE_SESSION if symbol.exchange == "NSE" else BSE_SESSION
            is_session_day = (
                self.holiday_calendar.is_session_day(symbol.exchange, now.astimezone(IST).date())
                if self.holiday_calendar else True
            )
            if not is_session_day or not session.is_open(now):
                await reject(symbol, 1, "MARKET_CLOSED", "Cash market session is closed")
                continue

            # Stage 2: a current, positive provider quote within price bounds (₹0 - ₹250).
            quote = await self.provider.get_quote(symbol)
            if self.quote_repo:
                await self.quote_repo.upsert(quote)
            if not quote.is_fresh(now, self.max_quote_age):
                await reject(symbol, 2, "STALE_MARKET_DATA", "Quote is outside the freshness window")
                continue
            if quote.price > self.price_ceiling or quote.price < self.price_floor:
                await reject(symbol, 2, "PRICE_OUT_OF_BOUNDS", "Quote is outside configured price bounds", {"price": str(quote.price)})
                continue
            affordable_quantity = self._affordable_quantity(symbol, quote.price)
            if affordable_quantity < 1:
                await reject(symbol, 2, "NOT_AFFORDABLE", "One whole share plus charges exceeds INR 1,000")
                continue

            if self.eligibility_engine is not None:
                instrument = self.instruments.get(symbol.canonical)
                if instrument is None:
                    await reject(symbol, 1, "INSTRUMENT_INELIGIBLE", "Instrument-master evidence is missing")
                    continue
                daily_candles = await self.provider.get_candles(symbol, "1d", 20)
                intraday_candles = await self.provider.get_candles(symbol, "1m", 20)
                eligibility = self.eligibility_engine.evaluate(
                    instrument, quote, daily_candles, intraday_candles, now
                )
                if not eligibility.eligible:
                    await reject(
                        symbol, 4, eligibility.code, "Strict liquidity, spread, circuit, or history gate failed",
                        eligibility.evidence,
                    )
                    continue

            # Stage 3: structurally valid, liquid candles with dynamic movement.
            candles = await self.provider.get_candles(symbol, "15m", 120)
            validated = self.validator.validate_series(candles, pair=symbol.canonical)
            turnover = sum(
                Decimal(str(c.get("close", 0))) * Decimal(str(c.get("volume", 0)))
                for c in validated.cleaned_candles[-20:]
            )
            if not validated.is_valid or len(validated.cleaned_candles) < 10 or turnover < Decimal("1000000"):
                await reject(symbol, 3, "INVALID_OR_ILLIQUID_CANDLES", "Candle history or turnover is insufficient")
                continue

            # Stage 4: Strategy Evaluation across the 5 approved strategies (STE, HDA, VCP, BBS, NR7)
            df = enrich_candles_df(validated.cleaned_candles)
            if df.empty:
                await reject(symbol, 4, "EMPTY_INDICATORS", "Indicator enrichment produced no rows")
                continue

            # Filter slow movement stocks (require dynamic ATR%)
            atr_pct = 0.0
            if len(df) >= 14:
                latest_candle = df.iloc[-1]
                atr_val = float(latest_candle.get("atr_14", 0.0))
                price_flt = float(quote.price)
                atr_pct = (atr_val / price_flt * 100) if price_flt > 0 else 0.0
                if self.min_atr_pct > 0 and atr_pct < self.min_atr_pct:
                    await reject(symbol, 4, "SLOW_MOVEMENT", "ATR percentage is below the configured threshold", {"atr_pct": atr_pct})
                    continue

            closes = [Decimal(str(c["close"])) for c in validated.cleaned_candles]
            momentum = ((closes[-1] / closes[0]) - 1) * 100 if closes and closes[0] > 0 else Decimal("0")
            spread = quote.spread_pct()
            spread_quality = max(Decimal("0"), Decimal("10") - (spread or Decimal("5")))
            evidence = RankingEvidence(
                liquidity=min(Decimal("20"), turnover / Decimal("1000000")),
                spread_quality=spread_quality,
                momentum=momentum,
                relative_strength=Decimal("0"),
                timeframe_alignment=Decimal("5") if momentum > 0 else Decimal("0"),
                volatility_suitability=Decimal(str(min(max(atr_pct, 0.0), 10.0))),
                data_quality=min(Decimal("10"), Decimal(len(validated.cleaned_candles)) / Decimal("12")),
            )
            candidate = ScannerCandidate(
                symbol=symbol,
                sector="UNKNOWN",
                penny_stock=quote.price < Decimal("1"),
                evidence_timestamp=quote.timestamp,
                evidence=evidence,
                affordability_preview=Decimal(affordable_quantity),
            )
            prepared[symbol.canonical] = (candidate, quote, df)

        ranked = self.ranker.rank([row[0] for row in prepared.values()], {"regime": "BULL"})
        await self.store.save_rankings(cycle_id, [
            {
                "canonical_symbol": candidate.symbol.canonical,
                "rank": index,
                "score": candidate.ranking_score,
                "evidence": {key: str(value) for key, value in asdict(candidate.evidence).items()},
            }
            for index, candidate in enumerate(ranked, start=1)
        ])

        # Expensive strategy and news pass is bounded to the ranked top 50.
        for candidate in ranked:
            symbol = candidate.symbol
            _, quote, df = prepared[symbol.canonical]
            triggered_signals = []
            for _, strat in self.strategies.items():
                sig = strat.evaluate(df, symbol)
                if sig and sig.score >= 85:
                    triggered_signals.append(sig)

            if not triggered_signals:
                await reject(symbol, 6, "LOW_CONFLUENCE", "No strategy reached the minimum score")
                continue

            # 30-Day News Sentiment & Governance Safety Check
            news_status = "SAFE"
            news_headline = ""
            if self.news_service:
                try:
                    news_res = await self.news_service.evaluate_symbol(symbol.trading_symbol)
                    news_status = news_res.get("status", "SAFE")
                    news_headline = news_res.get("headline", "")
                    if news_status == "UNKNOWN":
                        await reject(symbol, 7, "NEWS_UNKNOWN", "News evidence is unavailable; qualification held")
                        continue
                    if news_status == "HIGH_RISK":
                        await reject(symbol, 4, "NEGATIVE_NEWS_RISK", "Confirmed high-risk news event")
                        continue
                except Exception:
                    await reject(symbol, 7, "NEWS_UNKNOWN", "News evidence is unavailable; qualification held")
                    continue

            # Stage 8: freshness and affordability are rechecked immediately before publication.
            final_quote = await self.provider.get_quote(symbol)
            if not final_quote.is_fresh(now, self.max_quote_age) or self._affordable_quantity(symbol, final_quote.price) < 1:
                await reject(symbol, 8, "FINAL_RECHECK_FAILED", "Final quote freshness or affordability check failed")
                continue
            bucket = int(quote.timestamp.timestamp()) // 900
            sig = sorted(triggered_signals, key=lambda item: (-item.score, item.strategy_name))[0]
            signal_id = "sig-" + sha256(f"{symbol.canonical}:{sig.strategy_name}:{bucket}".encode()).hexdigest()[:20]
            inserted = await self.store.insert_signal_once(
                signal_id, symbol, sig.strategy_name, sig.score, final_quote.price,
                final_quote.timestamp.isoformat(), final_quote.source,
            )
            if inserted:
                result["qualified"] += 1
                result["signal_id"] = signal_id
                await self.store.record_scanner_decision(
                    cycle_id, symbol.canonical, 8, "QUALIFIED", "Candidate qualified for paper review",
                    {"rank_score": str(candidate.ranking_score), "strategy": sig.strategy_name,
                     "affordable_quantity": int(candidate.affordability_preview)}, now.isoformat(),
                )
                if self.telegram_client:
                    try:
                        import asyncio
                        asyncio.create_task(self.telegram_client.notify_scanner_signal(
                            symbol=symbol.canonical, strategy=sig.strategy_name,
                            price=float(final_quote.price), score=sig.score,
                            news_status=news_status, headline=news_headline,
                        ))
                    except Exception:
                        pass
            else:
                result["duplicates"] += 1
                await self.store.record_scanner_decision(
                    cycle_id, symbol.canonical, 8, "DUPLICATE_SIGNAL", "Signal already exists",
                    {"signal_id": signal_id}, now.isoformat(),
                )
            if result["qualified"] >= 10:
                break

        return result
