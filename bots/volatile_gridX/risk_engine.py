"""
PROJECT-ALPHA Risk Engine
"""

import logging
import time

from .config import PHASE5
from . import storage
from .market_analysis import analyze_coin_simple as analyze_coin

logger = logging.getLogger("vgx.risk_engine")

# ============================================================
# COOLDOWN
# ============================================================

cooldown_until = None

loss_streak = 0


def check_cooldown():

    global cooldown_until

    if cooldown_until:

        if time.time() < cooldown_until:

            remaining = int(

                cooldown_until - time.time()

            )

            return (

                True,

                f"Cooldown Active ({remaining}s)"

            )

    return False, "OK"


# ============================================================
# SIGNAL THRESHOLD
# ============================================================

def signal_threshold():

    return (

        PHASE5

        .get("signals", {})

        .get("min_score", 70)

    )


# ============================================================
# RISK CHECK
# ============================================================

def risk_check(score):

    max_positions = (

        PHASE5["trade"]

        .get("max_positions", 5)

    )

    if len(storage.positions) >= max_positions:

        return (

            False,

            "Maximum Positions Reached"

        )

    if score < signal_threshold():

        return (

            False,

            "Score Below Threshold"

        )

    return True, "PASSED"


# ============================================================
# MARKET INTELLIGENCE
# ============================================================

def market_intelligence():

    try:
        from bots.scanner_bot.scanner import get_watchlist
        wl = get_watchlist().get("coins", [])
        proxy_coin = wl[0] if wl else "BTC"
    except Exception:
        proxy_coin = "BTC"

    # BUGFIX (P0.3): reuse the scanner's existing in-process price-history
    # cache (bots/scanner_bot/main.py:_SCANNER.price_history) instead of
    # calling analyze_coin() with no history at all. No new fetch, no new
    # cache — this is the same in-process object the scanner already
    # maintains and that scanner_bridge.py already reads from directly.
    history = []
    try:
        import bots.scanner_bot.main as _scanner_main
        _scanner_instance = getattr(_scanner_main, "_SCANNER", None)
        if _scanner_instance is not None:
            history = list(_scanner_instance.price_history.get(proxy_coin, []))
    except Exception:
        logger.warning(
            "market_intelligence: failed to read scanner price history for %s",
            proxy_coin, exc_info=True
        )
        history = []

    if len(history) < 5:
        # Real history genuinely unavailable (scanner not warmed up yet, or
        # this coin isn't tracked yet). Fail safe: log it and return a
        # neutral reading rather than letting analyze_coin()'s own
        # insufficient-history fallback (score=50) get bucketed into BEAR
        # below, which previously blocked 100% of signals unconditionally.
        logger.warning(
            "market_intelligence: insufficient price history for %s (%d points) — "
            "returning neutral SIDEWAYS instead of misclassifying as BEAR",
            proxy_coin, len(history)
        )
        return {
            "regime": "SIDEWAYS",
            "score": 50
        }

    result = analyze_coin(

        proxy_coin,

        history

    )

    score = result.get(

        "score",

        0

    )

    if score >= 80:

        regime = "BULL"

    elif score >= 60:

        regime = "SIDEWAYS"

    else:

        regime = "BEAR"

    return {

        "regime": regime,

        "score": score

    }


# ============================================================
# MARKET FILTER
# ============================================================

def passes_market_intelligence_filter(

        coin

):

    market = market_intelligence()

    regime = market["regime"]

    if regime in [

        "BEAR",

        "HIGH_VOL"

    ]:

        return (

            False,

            f"{regime} Market"

        )

    return (

        True,

        "PASSED"

    )


# ============================================================
# POSITION CHECK
# ============================================================

def can_open_position(

        coin,

        score

):

    if coin in storage.positions:

        return (

            False,

            "Position Already Exists"

        )

    cd, msg = check_cooldown()

    if cd:

        return (

            False,

            msg

        )

    risk_ok, reason = risk_check(

        score

    )

    if not risk_ok:

        return (

            False,

            reason

        )

    market_ok, reason = (

        passes_market_intelligence_filter(

            coin

        )

    )

    if not market_ok:

        return (

            False,

            reason

        )

    return (

        True,

        "APPROVED"

    )


# ============================================================
# VALIDATE SIGNAL
# ============================================================

def validate_signal(

        signal

):

    coin = (

        signal

        .get("coin", "")

        .upper()

    )

    action = (

        signal

        .get("action", "")

        .upper()

    )

    score = signal.get(

        "score",

        0

    )

    if action != "BUY":

        return (

            False,

            "BUY ONLY",

            signal

        )

    # V1 Architecture: Bots accept all scanner signals and apply their own
    # strategy filters/risk checks. No per-bot watchlist rejection.
    allowed, reason = (

        can_open_position(

            coin,

            score

        )

    )

    return (

        allowed,

        reason,

        signal

    )
