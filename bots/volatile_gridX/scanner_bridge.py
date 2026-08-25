"""
PROJECT-ALPHA Scanner Bridge
Scanner Bot → Trading Bot Connector
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import PHASE5, VGX_MAX_SIGNAL_AGE_SECONDS
from . import storage
from .risk_engine import validate_signal
from .trading_engine import paper_execute_signal

logger = logging.getLogger("vgx.scanner_bridge")


# ============================================================
# SIGNAL AGE UTILITY
# ============================================================

def signal_age_seconds(signal: dict) -> float | None:
    ts_str = signal.get("timestamp")
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


# ============================================================
# SIGNAL QUEUES
# ============================================================

signal_queue = []

scanner_rejections = []


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
# NORMALIZE SIGNAL
# ============================================================

def normalize_signal(signal):

    if not isinstance(signal, dict):

        return None

    return {

        "coin":

            str(

                signal.get(

                    "coin",

                    ""

                )

            ).upper(),

        "action":

            str(

                signal.get(

                    "action",

                    "BUY"

                )

            ).upper(),

        "score":

            float(

                signal.get(

                    "score",

                    0

                )

            ),

        "source":

            signal.get(

                "source",

                "SCANNER"

            ),

        "timestamp":

            signal.get(

                "timestamp",

                time.time()

            )

    }


# ============================================================
# PROCESS SIGNAL
# ============================================================

def process_scanner_signal(signal):

    signal = normalize_signal(

        signal

    )

    if signal is None:

        return {

            "result": "REJECTED",

            "reason": "Invalid Payload"

        }

    coin = signal["coin"]

    action = signal["action"]

    score = signal["score"]

    # BUY ONLY

    if action != "BUY":

        reason = (

            "Only BUY Signals Allowed"

        )

        scanner_rejections.append({

            "coin": coin,

            "reason": reason,

            "time": time.time()

        })

        return {

            "result": "REJECTED",

            "reason": reason

        }


    # STALENESS CHECK

    age = signal_age_seconds(signal)

    if age is not None and age > VGX_MAX_SIGNAL_AGE_SECONDS:

        reason = f"Signal too old ({age:.0f}s > {VGX_MAX_SIGNAL_AGE_SECONDS}s)"

        scanner_rejections.append({

            "coin": coin,

            "reason": reason,

            "time": time.time()

        })

        return {

            "result": "REJECTED",

            "reason": reason

        }


    # VALIDATE SIGNAL

    accepted, reason, record = (

        validate_signal(

            signal

        )

    )

    if not accepted:

        scanner_rejections.append({

            "coin": coin,

            "reason": reason,

            "time": time.time()

        })

        return {

            "result": "REJECTED",

            "reason": reason

        }

    # EXECUTE TRADE

    executed, message = (

        paper_execute_signal(

            signal

        )

    )

    if executed:

        return {

            "result": "ACCEPTED",

            "reason": message

        }

    scanner_rejections.append({

        "coin": coin,

        "reason": message,

        "time": time.time()

    })

    return {

        "result": "REJECTED",

        "reason": message

    }


# ============================================================
# SCANNER FEED — pull signals from in-process module or API
# ============================================================

def _signals_from_module() -> list[dict]:
    """Read signals from the in-process scanner module (zero network cost)."""
    try:
        from bots.scanner_bot import main as scanner_main
    except Exception:
        return []
    signals = getattr(scanner_main, "LATEST_SCANNER_SIGNALS", None)
    if not signals:
        signals = getattr(scanner_main, "LATEST_MTB_SIGNALS", []) or []
    normalized = [normalize_signal(s) for s in signals if isinstance(s, dict)]
    return [s for s in normalized if s is not None]


def _signals_from_dashboard_api() -> list[dict]:
    """Fallback: pull recent_signals from the dashboard /api/v1/state endpoint."""
    url = os.getenv("SCANNER_API_URL", "http://localhost:5000")
    timeout = int(os.getenv("SCANNER_TIMEOUT_SECONDS", "5"))
    api_key = os.getenv("DASHBOARD_API_KEY", "")
    vgx_headers = {"Accept": "application/json"}
    if api_key:
        vgx_headers["X-API-Key"] = api_key
    req = urllib.request.Request(
        f"{url}/api/v1/state",
        headers=vgx_headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("VGX scanner API fetch failed: %s", exc)
        return []
    recent = payload.get("recent_signals", []) if isinstance(payload, dict) else []
    normalized = [normalize_signal(s) for s in recent if isinstance(s, dict)]
    return [s for s in normalized if s is not None]


def get_signals() -> list[dict]:
    """Return current scanner signals; in-process source takes priority."""
    module_signals = _signals_from_module()
    if module_signals:
        return module_signals
    return _signals_from_dashboard_api()


# ============================================================
# LEGACY ENTRY POINT
# ============================================================

def receive_signal(signal):

    return process_scanner_signal(

        signal

    )
