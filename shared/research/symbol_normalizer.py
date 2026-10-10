"""
Symbol normalizer for CoinResearchService.

Converts any user-supplied symbol string to canonical "BASE/QUOTE" form,
validates against the known CoinDCX pair universe, and provides the
searchable pairs list for the Research Hub autocomplete.
"""

from __future__ import annotations

# ── Known CoinDCX tradeable pairs ────────────────────────────────────────────

# ── Known CoinDCX tradeable pairs ────────────────────────────────────────────

SUPPORTED_PAIRS: list[str] = [
    # Top INR pairs
    "BTC/INR",
    "ETH/INR",
    "BNB/INR",
    "SOL/INR",
    "XRP/INR",
    "ADA/INR",
    "MATIC/INR",
    "POL/INR",
    "DOGE/INR",
    "TRX/INR",
    "SHIB/INR",
    "AVAX/INR",
    "LINK/INR",
    "DOT/INR",
    "LTC/INR",
    "ZEC/INR",
    "ATOM/INR",
    "FTM/INR",
    "NEAR/INR",
    "APT/INR",
    "SUI/INR",
    "FET/INR",
    "INJ/INR",
    "TIA/INR",
    "UNI/INR",
    "AAVE/INR",
    "SAND/INR",
    "MANA/INR",
    "GALA/INR",
    "ALGO/INR",
    "FIL/INR",
    "ICP/INR",
    "AR/INR",
    "OP/INR",
    "ARB/INR",
    "STX/INR",
    "IMX/INR",
    "MKR/INR",
    "SNX/INR",
    "LDO/INR",
    "QNT/INR",
    "EOS/INR",
    "FLOW/INR",
    "AXS/INR",
    "DYDX/INR",
    "CHZ/INR",
    "NEO/INR",
    "THETA/INR",
    "GRT/INR",
    "CRV/INR",
    "KAVA/INR",
    "1INCH/INR",
    "COMP/INR",
    "KSM/INR",
    # Top USDT pairs
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "MATIC/USDT",
    "POL/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "SHIB/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "LTC/USDT",
    "ZEC/USDT",
    "ATOM/USDT",
    "FTM/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "SUI/USDT",
    "FET/USDT",
    "INJ/USDT",
    "TIA/USDT",
    "RENDER/USDT",
    "PEPE/USDT",
    "KAS/USDT",
    "SEI/USDT",
    "TAO/USDT",
    "WIF/USDT",
    "BONK/USDT",
    "FLOKI/USDT",
    "BOME/USDT",
    "JUP/USDT",
    "PYTH/USDT",
    "PENDLE/USDT",
    "ENA/USDT",
    "W/USDT",
    "ONDO/USDT",
    "BEAM/USDT",
    "STRK/USDT",
    "DYM/USDT",
    "ALT/USDT",
    "MANTA/USDT",
    "ZRO/USDT",
    "IO/USDT",
    "NOT/USDT",
    "POPCAT/USDT",
    "MEW/USDT",
    "TURBO/USDT",
    "NEIRO/USDT",
    "AAVE/USDT",
    "UNI/USDT",
    "SAND/USDT",
    "MANA/USDT",
    "GALA/USDT",
    "ALGO/USDT",
    "FIL/USDT",
    "ICP/USDT",
    "AR/USDT",
    "OP/USDT",
    "ARB/USDT",
    "STX/USDT",
    "IMX/USDT",
    "MKR/USDT",
    "SNX/USDT",
]

# Quick lookup set
_SUPPORTED_SET: set[str] = set(SUPPORTED_PAIRS)

# Base → default quote mapping (prefer INR when available, unless USDT-only)
_BASE_DEFAULT_QUOTE: dict[str, str] = {
    "BTC": "INR",
    "ETH": "INR",
    "BNB": "INR",
    "SOL": "INR",
    "XRP": "INR",
    "ADA": "INR",
    "MATIC": "INR",
    "POL": "INR",
    "DOGE": "INR",
    "TRX": "INR",
    "SHIB": "INR",
    "AVAX": "INR",
    "LINK": "INR",
    "DOT": "INR",
    "LTC": "INR",
    "ZEC": "USDT",
    "ATOM": "INR",
    "FTM": "INR",
    "NEAR": "INR",
    "APT": "INR",
    "SUI": "INR",
    "FET": "INR",
    "RENDER": "USDT",
    "PEPE": "USDT",
    "KAS": "USDT",
    "SEI": "USDT",
    "TAO": "USDT",
    "WIF": "USDT",
    "BONK": "USDT",
    "FLOKI": "USDT",
    "BOME": "USDT",
    "JUP": "USDT",
    "PYTH": "USDT",
    "PENDLE": "USDT",
    "ENA": "USDT",
    "W": "USDT",
    "ONDO": "USDT",
    "BEAM": "USDT",
    "STRK": "USDT",
    "DYM": "USDT",
    "ALT": "USDT",
    "MANTA": "USDT",
    "ZRO": "USDT",
    "IO": "USDT",
    "NOT": "USDT",
    "POPCAT": "USDT",
    "MEW": "USDT",
    "TURBO": "USDT",
    "NEIRO": "USDT",
    "UNI": "INR",
    "AAVE": "INR",
}

_KNOWN_QUOTES: tuple[str, ...] = ("USDT", "INR", "BTC", "ETH", "BNB")


def normalize_symbol(raw: str) -> str:
    """
    Normalize any user-supplied symbol to canonical "BASE/QUOTE" form.

    Examples:
        "BTC"         -> "BTC/INR"
        "btc/inr"     -> "BTC/INR"
        "BTCINR"      -> "BTC/INR"
        "B-BTC_INR"   -> "BTC/INR"
        "BTC-INR"     -> "BTC/INR"
        "SOL/USDT"    -> "SOL/USDT"
    """
    s = raw.strip().upper()

    # Already canonical with slash
    if "/" in s:
        parts = s.split("/", 1)
        return f"{parts[0]}/{parts[1]}"

    # CoinDCX wire format: "B-BTC_INR" or "I-BTC_INR"
    if s.startswith(("B-", "I-")):
        s = s[2:]  # strip prefix
        if "_" in s:
            base, quote = s.split("_", 1)
            return f"{base}/{quote}"

    # Hyphen separator: "BTC-INR"
    if "-" in s:
        parts = s.split("-", 1)
        return f"{parts[0]}/{parts[1]}"

    # Underscore separator: "BTC_INR"
    if "_" in s:
        parts = s.split("_", 1)
        return f"{parts[0]}/{parts[1]}"

    # No separator — try to split off known quote currencies
    for quote in _KNOWN_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            base = s[: -len(quote)]
            return f"{base}/{quote}"

    # Bare base symbol — apply default quote
    if s in _BASE_DEFAULT_QUOTE:
        return f"{s}/{_BASE_DEFAULT_QUOTE[s]}"

    # Unknown — return as-is with INR fallback
    return f"{s}/INR"


def is_supported_pair(pair: str) -> bool:
    """Return True if the canonical pair is in the known CoinDCX universe."""
    canonical = normalize_symbol(pair)
    return canonical in _SUPPORTED_SET


def get_supported_pairs_info() -> list[dict]:
    """Return list of {pair, base, quote} dicts for the Research Hub autocomplete."""
    result = []
    for pair in SUPPORTED_PAIRS:
        base, quote = pair.split("/")
        result.append({"pair": pair, "base": base, "quote": quote})
    return result


def resolve_tradeable_pairs(raw_symbol: str) -> dict:
    """
    Resolve all tradeable quote pairs for a given base asset.
    Prioritizes INR if available in the supported universe, otherwise falls back to USDT.
    """
    raw = raw_symbol.strip().upper()
    # Clean symbol prefix and separators
    base = raw.replace("B-", "").replace("I-", "")
    if "/" in base:
        base = base.split("/")[0]
    elif "_" in base:
        base = base.split("_")[0]
    elif "-" in base:
        base = base.split("-")[0]
    elif base.endswith("INR") and len(base) > 3:
        base = base[:-3]
    elif base.endswith("USDT") and len(base) > 4:
        base = base[:-4]

    base = base.strip()

    inr_pair = f"{base}/INR"
    usdt_pair = f"{base}/USDT"

    available = []
    if inr_pair in _SUPPORTED_SET:
        available.append(inr_pair)
    if usdt_pair in _SUPPORTED_SET:
        available.append(usdt_pair)

    # If neither in static set, assume both could be queried, prioritizing default mapping
    if not available:
        default_quote = _BASE_DEFAULT_QUOTE.get(base, "INR")
        if default_quote == "USDT":
            available = [usdt_pair, inr_pair]
        else:
            available = [inr_pair, usdt_pair]

    default_quote = _BASE_DEFAULT_QUOTE.get(base, "INR")
    if inr_pair in available and default_quote != "USDT":
        primary = inr_pair
    elif usdt_pair in available:
        primary = usdt_pair
    else:
        primary = available[0] if available else inr_pair

    preferred_quote = "INR" if primary.endswith("/INR") else "USDT"

    return {
        "base_asset": base,
        "primary_pair": primary,
        "available_pairs": available,
        "preferred_quote": preferred_quote,
        "has_inr": inr_pair in available,
        "has_usdt": usdt_pair in available,
    }


def is_supported_symbol(raw: str) -> bool:
    """Alias for is_supported_pair."""
    return is_supported_pair(raw)


def get_base_asset(raw: str) -> str:
    """Extract base asset symbol (e.g. SOL from SOL/INR or SOLUSDT)."""
    return resolve_tradeable_pairs(raw)["base_asset"]


def get_preferred_pair_for_base(raw: str) -> str:
    """Return the primary tradeable pair for the base asset."""
    return resolve_tradeable_pairs(raw)["primary_pair"]


def get_quote_currency(raw: str) -> str:
    """Extract quote currency (e.g. INR or USDT)."""
    canonical = normalize_symbol(raw)
    if "/" in canonical:
        return canonical.split("/")[1]
    return "INR"
