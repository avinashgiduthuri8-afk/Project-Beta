"""
PROJECT-ALPHA Market Analysis Engine
Production replacement for analyze_coin stub.

Provides real market analysis using:
- Historical scanner data
- Trend strength calculation
- Volume profile analysis
- Volatility measurement
- Market regime detection
- Coin scoring
"""

import logging
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime, timezone

logger = logging.getLogger("vgx.market_analysis")

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class TrendAnalysis:
    """Trend strength and direction analysis."""
    direction: str          # "bullish", "bearish", "neutral"
    strength: float         # 0-100
    ema_fast: float
    ema_slow: float
    ema_crossover: str      # "bullish_cross", "bearish_cross", "none"
    momentum: float         # Rate of change


@dataclass
class VolumeProfile:
    """Volume analysis metrics."""
    current_volume: float
    average_volume: float
    volume_ratio: float     # current / average
    is_spike: bool          # > 2x average
    trend: str              # "increasing", "decreasing", "stable"


@dataclass
class VolatilityMetrics:
    """Volatility analysis."""
    current_volatility: float   # Average % move
    historical_volatility: float
    volatility_ratio: float     # current / historical
    is_high_volatility: bool    # > 1.8x historical
    risk_level: str             # "low", "medium", "high", "extreme"


@dataclass
class MarketRegime:
    """Market regime classification."""
    regime: str             # "bull_trend", "bear_trend", "sideways", "breakout", "pullback", "recovery"
    confidence: int         # 0-100
    favorable_for_entry: bool


@dataclass
class CoinAnalysisResult:
    """Complete coin analysis result."""
    coin: str
    score: int              # 0-100 overall score
    trend: TrendAnalysis
    volume: VolumeProfile
    volatility: VolatilityMetrics
    regime: MarketRegime
    recommendation: str     # "strong_buy", "buy", "hold", "avoid", "sell"
    reasons: List[str]
    timestamp: str


# ============================================================
# ANALYSIS FUNCTIONS
# ============================================================

def calculate_ema(prices: List[float], period: int) -> List[float]:
    """Calculate Exponential Moving Average."""
    if not prices or len(prices) < period:
        return []
    
    multiplier = 2 / (period + 1)
    result = [prices[0]]
    
    for price in prices[1:]:
        ema = (price - result[-1]) * multiplier + result[-1]
        result.append(ema)
    
    return result


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return 50.0  # Neutral default
    
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.0001  # Avoid division by zero
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return round(rsi, 2)


def calculate_volatility(prices: List[float], period: int = 20) -> float:
    """Calculate average percentage volatility."""
    if len(prices) < 2:
        return 0.0
    
    moves = []
    window = prices[-period:] if len(prices) >= period else prices
    
    for i in range(1, len(window)):
        if window[i-1] > 0:
            pct_move = abs((window[i] - window[i-1]) / window[i-1]) * 100
            moves.append(pct_move)
    
    return sum(moves) / len(moves) if moves else 0.0


def analyze_trend(history: List[Dict]) -> TrendAnalysis:
    """Analyze price trend using EMA and momentum."""
    if len(history) < 2:
        return TrendAnalysis(
            direction="neutral",
            strength=50,
            ema_fast=0,
            ema_slow=0,
            ema_crossover="none",
            momentum=0
        )
    
    prices = [h.get("price", h) if isinstance(h, dict) else h for h in history]
    
    # Calculate EMAs
    ema_fast_values = calculate_ema(prices, 9)
    ema_slow_values = calculate_ema(prices, 21)
    
    ema_fast = ema_fast_values[-1] if ema_fast_values else prices[-1]
    ema_slow = ema_slow_values[-1] if ema_slow_values else prices[-1]
    
    # Detect crossover
    crossover = "none"
    if len(ema_fast_values) >= 2 and len(ema_slow_values) >= 2:
        prev_fast = ema_fast_values[-2]
        prev_slow = ema_slow_values[-2]
        
        if prev_fast <= prev_slow and ema_fast > ema_slow:
            crossover = "bullish_cross"
        elif prev_fast >= prev_slow and ema_fast < ema_slow:
            crossover = "bearish_cross"
    
    # Calculate momentum (rate of change)
    lookback = min(10, len(prices) - 1)
    if lookback > 0 and prices[-lookback-1] > 0:
        momentum = ((prices[-1] - prices[-lookback-1]) / prices[-lookback-1]) * 100
    else:
        momentum = 0
    
    # Determine direction
    if ema_fast > ema_slow and momentum > 0.5:
        direction = "bullish"
    elif ema_fast < ema_slow and momentum < -0.5:
        direction = "bearish"
    else:
        direction = "neutral"
    
    # Calculate strength (0-100)
    ema_separation = abs(ema_fast - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0
    momentum_strength = min(abs(momentum) * 5, 50)  # Cap at 50
    strength = min(100, int(ema_separation * 10 + momentum_strength))
    
    return TrendAnalysis(
        direction=direction,
        strength=strength,
        ema_fast=round(ema_fast, 6),
        ema_slow=round(ema_slow, 6),
        ema_crossover=crossover,
        momentum=round(momentum, 2)
    )


def analyze_volume(history: List[Dict], period: int = 20) -> VolumeProfile:
    """Analyze volume profile."""
    if not history:
        return VolumeProfile(
            current_volume=0,
            average_volume=0,
            volume_ratio=1.0,
            is_spike=False,
            trend="stable"
        )
    
    volumes = []
    for h in history:
        if isinstance(h, dict):
            vol = h.get("volume", 0)
        else:
            vol = 0
        volumes.append(vol)
    
    current_volume = volumes[-1] if volumes else 0
    
    # Calculate average (excluding current)
    historical = volumes[:-1] if len(volumes) > 1 else volumes
    avg_volume = sum(historical[-period:]) / min(len(historical), period) if historical else 1
    
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
    is_spike = volume_ratio > 2.0
    
    # Determine trend
    if len(volumes) >= 5:
        recent_avg = sum(volumes[-3:]) / 3
        older_avg = sum(volumes[-8:-3]) / 5 if len(volumes) >= 8 else recent_avg
        
        if recent_avg > older_avg * 1.2:
            trend = "increasing"
        elif recent_avg < older_avg * 0.8:
            trend = "decreasing"
        else:
            trend = "stable"
    else:
        trend = "stable"
    
    return VolumeProfile(
        current_volume=round(current_volume, 2),
        average_volume=round(avg_volume, 2),
        volume_ratio=round(volume_ratio, 2),
        is_spike=is_spike,
        trend=trend
    )


def analyze_volatility(history: List[Dict]) -> VolatilityMetrics:
    """Analyze volatility metrics."""
    if len(history) < 5:
        return VolatilityMetrics(
            current_volatility=0,
            historical_volatility=0,
            volatility_ratio=1.0,
            is_high_volatility=False,
            risk_level="medium"
        )
    
    prices = [h.get("price", h) if isinstance(h, dict) else h for h in history]
    
    # Current volatility (last 5 periods)
    current_vol = calculate_volatility(prices, 5)
    
    # Historical volatility (last 20 periods)
    historical_vol = calculate_volatility(prices, 20)
    
    vol_ratio = current_vol / historical_vol if historical_vol > 0 else 1.0
    is_high_vol = vol_ratio > 1.8
    
    # Risk level classification
    if current_vol < 1.0:
        risk_level = "low"
    elif current_vol < 2.5:
        risk_level = "medium"
    elif current_vol < 5.0:
        risk_level = "high"
    else:
        risk_level = "extreme"
    
    return VolatilityMetrics(
        current_volatility=round(current_vol, 2),
        historical_volatility=round(historical_vol, 2),
        volatility_ratio=round(vol_ratio, 2),
        is_high_volatility=is_high_vol,
        risk_level=risk_level
    )


def detect_market_regime(
    trend: TrendAnalysis,
    volume: VolumeProfile,
    volatility: VolatilityMetrics,
    prices: List[float]
) -> MarketRegime:
    """Detect current market regime."""
    
    if len(prices) < 6:
        return MarketRegime(
            regime="sideways",
            confidence=50,
            favorable_for_entry=False
        )
    
    regime = "sideways"
    confidence = 50
    favorable = False
    
    # Breakout detection
    if volume.is_spike and trend.direction == "bullish" and trend.momentum > 2:
        regime = "breakout"
        confidence = min(90, 50 + int(volume.volume_ratio * 10) + trend.strength // 2)
        favorable = True
    
    # Bull trend
    elif trend.direction == "bullish" and trend.strength > 60:
        regime = "bull_trend"
        confidence = trend.strength
        favorable = True
    
    # Pullback in uptrend
    elif trend.ema_fast > trend.ema_slow and trend.momentum < 0:
        regime = "pullback"
        confidence = 60
        favorable = True  # Good entry opportunity
    
    # Recovery
    elif trend.ema_crossover == "bullish_cross":
        regime = "recovery"
        confidence = 70
        favorable = True
    
    # Bear trend
    elif trend.direction == "bearish" and trend.strength > 60:
        regime = "bear_trend"
        confidence = trend.strength
        favorable = False
    
    # High volatility caution
    if volatility.is_high_volatility and volatility.risk_level in ("high", "extreme"):
        confidence = max(30, confidence - 20)
        if regime not in ("breakout",):
            favorable = False
    
    return MarketRegime(
        regime=regime,
        confidence=confidence,
        favorable_for_entry=favorable
    )


def calculate_coin_score(
    trend: TrendAnalysis,
    volume: VolumeProfile,
    volatility: VolatilityMetrics,
    regime: MarketRegime,
    rsi: float
) -> int:
    """Calculate overall coin score (0-100)."""
    score = 50  # Base score
    
    # Trend contribution (max +/- 25)
    if trend.direction == "bullish":
        score += min(25, trend.strength // 4)
    elif trend.direction == "bearish":
        score -= min(25, trend.strength // 4)
    
    # EMA crossover bonus
    if trend.ema_crossover == "bullish_cross":
        score += 10
    elif trend.ema_crossover == "bearish_cross":
        score -= 10
    
    # Volume contribution (max +/- 15)
    if volume.is_spike and trend.direction == "bullish":
        score += 15
    elif volume.trend == "increasing" and trend.direction == "bullish":
        score += 8
    elif volume.trend == "decreasing":
        score -= 5
    
    # RSI contribution (max +/- 10)
    if 30 < rsi < 50 and trend.direction == "bullish":
        score += 10  # Oversold recovery
    elif rsi > 70:
        score -= 10  # Overbought risk
    elif rsi < 30:
        score += 5   # Potential reversal
    
    # Regime contribution
    if regime.favorable_for_entry:
        score += 10
    elif regime.regime == "bear_trend":
        score -= 15
    
    # Volatility penalty
    if volatility.risk_level == "extreme":
        score -= 15
    elif volatility.risk_level == "high":
        score -= 8
    
    # Clamp to 0-100
    return max(0, min(100, score))


def get_recommendation(score: int, regime: MarketRegime, volatility: VolatilityMetrics) -> str:
    """Generate trading recommendation based on analysis."""
    if volatility.risk_level == "extreme":
        return "avoid"
    
    if regime.regime == "bear_trend":
        return "avoid"
    
    if score >= 80 and regime.favorable_for_entry:
        return "strong_buy"
    elif score >= 65 and regime.favorable_for_entry:
        return "buy"
    elif score >= 50:
        return "hold"
    elif score >= 35:
        return "avoid"
    else:
        return "sell"


# ============================================================
# MAIN ANALYSIS FUNCTION
# ============================================================

def analyze_coin(coin: str, history: Optional[List[Dict]] = None) -> CoinAnalysisResult:
    """
    Comprehensive coin analysis replacing the dummy stub.
    
    Args:
        coin: Coin symbol (e.g., "BTC")
        history: Price/volume history list. Each item should have 'price' and optionally 'volume'
        
    Returns:
        CoinAnalysisResult with complete analysis
    """
    if history is None:
        history = []
    
    # Handle empty or insufficient history
    if len(history) < 5:
        return CoinAnalysisResult(
            coin=coin,
            score=50,
            trend=TrendAnalysis("neutral", 50, 0, 0, "none", 0),
            volume=VolumeProfile(0, 0, 1.0, False, "stable"),
            volatility=VolatilityMetrics(0, 0, 1.0, False, "medium"),
            regime=MarketRegime("sideways", 50, False),
            recommendation="hold",
            reasons=["Insufficient price history for analysis"],
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    
    # Extract prices
    prices = []
    for h in history:
        if isinstance(h, dict):
            prices.append(h.get("price", 0))
        else:
            prices.append(float(h))
    
    # Run all analyses
    trend = analyze_trend(history)
    volume = analyze_volume(history)
    volatility = analyze_volatility(history)
    regime = detect_market_regime(trend, volume, volatility, prices)
    rsi = calculate_rsi(prices)
    
    # Calculate overall score
    score = calculate_coin_score(trend, volume, volatility, regime, rsi)
    
    # Get recommendation
    recommendation = get_recommendation(score, regime, volatility)
    
    # Build reasons
    reasons = []
    
    if trend.ema_crossover == "bullish_cross":
        reasons.append("Bullish EMA crossover detected")
    elif trend.ema_crossover == "bearish_cross":
        reasons.append("Bearish EMA crossover - caution")
    
    if trend.direction == "bullish" and trend.strength > 60:
        reasons.append(f"Strong uptrend (strength: {trend.strength})")
    elif trend.direction == "bearish" and trend.strength > 60:
        reasons.append(f"Strong downtrend (strength: {trend.strength})")
    
    if volume.is_spike:
        reasons.append(f"Volume spike ({volume.volume_ratio:.1f}x average)")
    
    if regime.regime == "breakout":
        reasons.append("Potential breakout forming")
    elif regime.regime == "pullback":
        reasons.append("Pullback in uptrend - entry opportunity")
    
    if volatility.risk_level in ("high", "extreme"):
        reasons.append(f"High volatility warning ({volatility.risk_level})")
    
    if rsi < 30:
        reasons.append(f"Oversold (RSI: {rsi:.0f})")
    elif rsi > 70:
        reasons.append(f"Overbought (RSI: {rsi:.0f})")
    
    if not reasons:
        reasons.append("Neutral market conditions")
    
    return CoinAnalysisResult(
        coin=coin,
        score=score,
        trend=trend,
        volume=volume,
        volatility=volatility,
        regime=regime,
        recommendation=recommendation,
        reasons=reasons,
        timestamp=datetime.now(timezone.utc).isoformat()
    )


# ============================================================
# LEGACY COMPATIBILITY
# ============================================================

def analyze_coin_simple(coin: str, history: Optional[List] = None) -> dict:
    """
    Simple dict-returning version for backward compatibility.
    Replaces the original stub that returned {"score": 75, "trend": "neutral", ...}
    """
    result = analyze_coin(coin, history)
    
    return {
        "score": result.score,
        "trend": result.trend.direction,
        "rsi": calculate_rsi([h.get("price", h) if isinstance(h, dict) else h for h in (history or [])]),
        "ema": "bullish" if result.trend.ema_fast > result.trend.ema_slow else "bearish" if result.trend.ema_fast < result.trend.ema_slow else "flat",
        "regime": result.regime.regime,
        "recommendation": result.recommendation,
        "volatility": result.volatility.risk_level,
        "favorable_entry": result.regime.favorable_for_entry,
    }
