from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from market.holidays import HolidayCalendar
from market.models import MarketQuote
from market.symbols import StockSymbol
from market.universe import NseUniverse, UniverseInstrument
from scanner.eligibility import EligibilityEngine


def instrument(trading_symbol="TEST", **overrides):
    values = dict(
        symbol=StockSymbol.parse(f"NSE:{trading_symbol}"), series="EQ", instrument_type="EQUITY",
        sector="TEST", tick_size=Decimal("0.05"), trading_status="ACTIVE", is_sme=False,
        surveillance_status="NONE", price_band_pct=Decimal("20"), circuit_locked_sessions=0,
    )
    values.update(overrides)
    return UniverseInstrument(**values)


def candles(price, turnover, count=20, volume_zero=False):
    volume = Decimal("0") if volume_zero else Decimal(turnover) / Decimal(price)
    return [
        {"timestamp": 1_700_000_000_000 + index * 86_400_000, "open": price,
         "high": Decimal(price) * Decimal("1.002"), "low": Decimal(price) * Decimal("0.998"),
         "close": price, "volume": volume}
        for index in range(count)
    ]


def test_nse_universe_excludes_nonstandard_or_restricted_instruments():
    rows = [
        instrument("GOOD"),
        instrument("BSE", symbol=StockSymbol.parse("BSE:500001")),
        instrument("SME", is_sme=True),
        instrument("SUSP", trading_status="SUSPENDED"),
        instrument("ETF", instrument_type="ETF"),
        instrument("BE", series="BE"),
    ]
    assert [item.symbol.canonical for item in NseUniverse(lambda: rows).refresh()] == ["NSE:GOOD"]


def test_holiday_calendar_fails_closed_without_current_year_coverage():
    calendar = HolidayCalendar({"NSE": {date(2026, 10, 20)}})
    assert calendar.is_session_day("NSE", date(2026, 10, 19))
    assert not calendar.is_session_day("NSE", date(2026, 10, 20))
    assert not calendar.is_session_day("NSE", date(2027, 10, 19))


def test_configured_holiday_calendar_covers_nse_and_bse_independently():
    calendar = HolidayCalendar.from_json("config/nse_holidays.json")
    assert calendar.is_session_day("NSE", date(2026, 10, 19))
    assert calendar.is_session_day("BSE", date(2026, 10, 19))
    assert not calendar.is_session_day("NSE", date(2026, 10, 20))
    assert not calendar.is_session_day("BSE", date(2026, 10, 20))


def test_price_boundaries_and_stricter_penny_liquidity():
    now = datetime(2026, 10, 16, 5, tzinfo=timezone.utc)
    engine = EligibilityEngine(max_quote_age=timedelta(minutes=15))
    for price in (Decimal("0.25"), Decimal("0.99"), Decimal("1"), Decimal("250")):
        required = Decimal("10000000") if price < 1 else Decimal("5000000")
        quote = MarketQuote(instrument().symbol, price, now, "TEST", bid=price, ask=price)
        assert engine.evaluate(instrument(), quote, candles(price, required), candles(price, required), now).eligible

    expensive = MarketQuote(instrument().symbol, Decimal("250.01"), now, "TEST")
    assert engine.evaluate(instrument(), expensive, [], [], now).code == "PRICE_ABOVE_LIMIT"
    penny = Decimal("0.25")
    illiquid = MarketQuote(instrument().symbol, penny, now, "TEST", bid=penny, ask=penny)
    assert engine.evaluate(instrument(), illiquid, candles(penny, 9_999_999), candles(penny, 9_999_999), now).code == "INSUFFICIENT_LIQUIDITY"


def test_spread_history_volume_circuit_and_staleness_fail_closed():
    now = datetime(2026, 10, 16, 5, tzinfo=timezone.utc)
    engine = EligibilityEngine(max_quote_age=timedelta(minutes=15))
    item = instrument()
    daily = candles(100, 5_000_000)
    intraday = candles(100, 5_000_000)
    wide = MarketQuote(item.symbol, Decimal("100"), now, "TEST", bid=Decimal("99"), ask=Decimal("101"))
    assert engine.evaluate(item, wide, daily, intraday, now).code == "SPREAD_TOO_WIDE"
    unknown = MarketQuote(item.symbol, Decimal("100"), now, "TEST")
    assert engine.evaluate(item, unknown, daily, intraday[:19], now).code == "SPREAD_UNKNOWN"
    assert engine.evaluate(item, unknown, daily[:19], intraday, now).code == "INSUFFICIENT_HISTORY"
    zero_volume = candles(100, 5_000_000, volume_zero=True)
    assert engine.evaluate(item, unknown, daily, zero_volume, now).code == "INSUFFICIENT_LIQUIDITY"
    assert engine.evaluate(instrument(circuit_locked_sessions=2), unknown, daily, intraday, now).code == "CIRCUIT_RISK"
    stale = MarketQuote(item.symbol, Decimal("100"), now - timedelta(hours=1), "TEST", bid=99, ask=100)
    assert engine.evaluate(item, stale, daily, intraday, now).code == "STALE_QUOTE"
