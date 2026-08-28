"""Multi-Timeframe Setup Scanner Engine for Indian Equities (Project-Beta)."""

from __future__ import annotations

import logging
from typing import List, Optional, Dict, Any
from core.models import ScannerCandidate, Candle, ScoreBreakdown
from core.enums import Exchange, SetupType
from scanner.setups import SetupDetector
from scanner.indicators import Indicators
from scoring.scorecard import SignalScorecard
from session.market_clock import IndianMarketSession
from data.provider import DataProviderManager

logger = logging.getLogger(__name__)


class MultiTimeframeScanner:
    """Scans Indian stock universe across Daily Trend -> 1H Setup -> 15M Trigger pipeline."""

    def __init__(self, data_manager: Optional[DataProviderManager] = None, session_guard: Optional[IndianMarketSession] = None):
        self.data_manager = data_manager or DataProviderManager()
        self.session_guard = session_guard or IndianMarketSession()

    def scan_symbol(
        self,
        symbol: str,
        sector: str = "GENERAL",
        exchange: Exchange = Exchange.NSE,
        min_score: float = 70.0,
    ) -> Optional[ScannerCandidate]:
        """Evaluate a single stock across the full MTF hierarchy."""
        # 1. Fetch historical data across timeframes
        daily_candles = self.data_manager.get_candles(symbol, timeframe="1d", lookback_days=250)
        h1_candles = self.data_manager.get_candles(symbol, timeframe="1h", lookback_days=30)
        m15_candles = self.data_manager.get_candles(symbol, timeframe="15m", lookback_days=10)
        benchmark_candles = self.data_manager.primary_provider.get_benchmark_candles() if hasattr(self.data_manager.primary_provider, "get_benchmark_candles") else daily_candles

        if not daily_candles or len(daily_candles) < 50:
            return None

        current_candle = daily_candles[-1]
        current_price = current_candle.close

        # 2. Liquidity & Universe Pre-Filter
        delivery_info = self.data_manager.primary_provider.get_delivery_data(symbol)
        delivery_pct = delivery_info.get("delivery_percentage", 45.0)
        avg_delivery_pct = delivery_info.get("avg_20d_delivery_percentage", 40.0)

        is_liquid, reason = self.session_guard.validate_stock_liquidity(current_price, current_candle.volume, avg_delivery_pct)
        if not is_liquid:
            return None

        # 3. Indicator Computations
        ema_20 = Indicators.calculate_ema(daily_candles, 20)
        ema_50 = Indicators.calculate_ema(daily_candles, 50)
        ema_200 = Indicators.calculate_ema(daily_candles, 200)
        atr_14 = Indicators.calculate_atr(daily_candles, 14)
        mansfield_rs = Indicators.calculate_mansfield_rs(daily_candles, benchmark_candles, period=50)

        # 4. Setup Detections
        detected_setup: Optional[SetupType] = None
        setup_meta: Dict[str, Any] = {}

        # Test VCP
        is_vcp, vcp_meta = SetupDetector.detect_minervini_vcp(daily_candles)
        if is_vcp:
            detected_setup = SetupType.MINERVINI_VCP
            setup_meta = vcp_meta
        else:
            # Test High Delivery Breakout
            is_del, del_meta = SetupDetector.detect_high_delivery_breakout(daily_candles, delivery_pct, avg_delivery_pct)
            if is_del:
                detected_setup = SetupType.HIGH_DELIVERY_BREAKOUT
                setup_meta = del_meta
            else:
                # Test Pocket Pivot
                is_pp, pp_meta = SetupDetector.detect_pocket_pivot(daily_candles)
                if is_pp:
                    detected_setup = SetupType.POCKET_PIVOT
                    setup_meta = pp_meta
                else:
                    # Test NR7 Squeeze
                    is_nr7, nr7_meta = SetupDetector.detect_nr7_squeeze(daily_candles)
                    if is_nr7:
                        detected_setup = SetupType.NR7_SQUEEZE
                        setup_meta = nr7_meta

        if not detected_setup:
            return None

        # 5. Scorecard Evaluation
        score = SignalScorecard.evaluate(
            daily_candles=daily_candles,
            h1_candles=h1_candles,
            m15_candles=m15_candles,
            setup_type=detected_setup,
            setup_metadata=setup_meta,
            mansfield_rs=mansfield_rs,
            delivery_pct=delivery_pct,
            avg_delivery_pct=avg_delivery_pct,
        )

        if not score.hard_gates_passed or score.total_score < min_score:
            return None

        logger.info(f"[Scanner] ✨ Qualified Candidate Found: {symbol} | Setup={detected_setup.value} | Score={score.total_score}/100 | RS={mansfield_rs:+.1f}")

        return ScannerCandidate(
            symbol=symbol,
            exchange=exchange,
            sector=sector,
            ltp=current_price,
            setup_type=detected_setup,
            score_breakdown=score,
            relative_strength_vs_nifty=mansfield_rs,
            daily_volume=current_candle.volume,
            delivery_pct=delivery_pct,
            atr_14=atr_14,
            ema_20=ema_20,
            ema_50=ema_50,
            ema_200=ema_200,
        )

    def scan_universe(self, universe: List[Dict[str, str]], min_score: float = 70.0) -> List[ScannerCandidate]:
        """Scan a list of universe stocks and rank candidates by total score."""
        candidates = []
        for stock in universe:
            sym = stock.get("symbol", "")
            sec = stock.get("sector", "GENERAL")
            cand = self.scan_symbol(sym, sector=sec, min_score=min_score)
            if cand:
                candidates.append(cand)

        # Sort descending by scorecard total score
        candidates.sort(key=lambda c: c.score_breakdown.total_score, reverse=True)
        return candidates
