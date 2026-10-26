"""Unified, read-only analytics service for Project Beta."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from backtesting.engine import BacktestResult
from core.database import open_beta_database
from market.metadata import Exchange, Market, get_exchange_metadata


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ANALYTICS_DATETIME_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


@dataclass(frozen=True, slots=True)
class AnalyticsFilter:
    market: str | None = None
    exchange: str | None = None
    symbol: str | None = None
    strategy: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        market = str(self.market).strip().upper() if self.market is not None else None
        exchange = str(self.exchange).strip().upper() if self.exchange is not None else None
        symbol = str(self.symbol).strip().upper() if self.symbol is not None else None
        strategy = str(self.strategy).strip().upper() if self.strategy is not None else None
        if market is not None:
            try:
                market = Market(market).value
            except ValueError:
                raise ValueError("UNSUPPORTED_ANALYTICS_MARKET") from None
        if exchange is not None:
            try:
                exchange = Exchange(exchange).value
            except ValueError:
                raise ValueError("UNSUPPORTED_ANALYTICS_EXCHANGE") from None
            exchange_market = get_exchange_metadata(exchange).market.value
            if market is not None and market != exchange_market:
                raise ValueError("ANALYTICS_MARKET_EXCHANGE_MISMATCH")
        if symbol == "" or strategy == "":
            raise ValueError("INVALID_ANALYTICS_FILTER")
        start = _utc(self.start) if self.start is not None else None
        end = _utc(self.end) if self.end is not None else None
        if start is not None and end is not None and start > end:
            raise ValueError("INVALID_ANALYTICS_DATE_RANGE")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "strategy", strategy)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    market: str
    exchange: str
    symbol: str
    strategy: str
    started_at: datetime
    ended_at: datetime
    result: BacktestResult

    def __post_init__(self) -> None:
        market = Market(str(self.market).strip().upper()).value
        exchange = Exchange(str(self.exchange).strip().upper()).value
        if get_exchange_metadata(exchange).market.value != market:
            raise ValueError("BACKTEST_MARKET_EXCHANGE_MISMATCH")
        started_at = _utc(self.started_at)
        ended_at = _utc(self.ended_at)
        if started_at > ended_at:
            raise ValueError("INVALID_BACKTEST_RUN_RANGE")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(self, "strategy", str(self.strategy).strip().upper())
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)


class AnalyticsService:
    """Build JSON-ready views without owning any mutable trading state."""

    access_mode = "READ_ONLY"

    def __init__(
        self,
        database_path: str,
        *,
        backtests: Iterable[BacktestRun] = (),
        freshness_window: timedelta = timedelta(minutes=15),
    ) -> None:
        if freshness_window <= timedelta(0):
            raise ValueError("INVALID_ANALYTICS_FRESHNESS_WINDOW")
        self._database_path = str(Path(database_path).resolve())
        self._backtests = tuple(backtests)
        self._freshness_window = freshness_window

    async def snapshot(
        self,
        filters: AnalyticsFilter | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        query = filters or AnalyticsFilter()
        observed_at = _utc(now or datetime.now(timezone.utc))
        conn = await open_beta_database(self._database_path, read_only=True)
        try:
            signals = await self._signals(conn, query, qualified_only=True)
            signal_history = await self._signals(conn, query, qualified_only=False)
            rankings = await self._rankings(conn, query)
            health = await self._health(conn, query, observed_at)
        finally:
            await conn.close()
        backtests = self._backtest_results(query)
        return {
            "generated_at": observed_at.isoformat(),
            "access_mode": self.access_mode,
            "signals": signals,
            "signal_history": signal_history,
            "rankings": rankings,
            "backtests": backtests,
            "health": health,
        }

    async def _signals(
        self, conn: Any, query: AnalyticsFilter, *, qualified_only: bool
    ) -> dict[str, Any]:
        clauses, values = self._persisted_filters(query, "exchange", "symbol", "market_timestamp")
        if qualified_only:
            clauses.append("status='QUALIFIED'")
        if query.strategy is not None:
            clauses.append("strategy=?")
            values.append(query.strategy)
        sql = "SELECT * FROM beta_signals"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY market_timestamp, id"
        async with conn.execute(sql, values) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        items = []
        for row in rows:
            item = dict(row)
            item["market"] = get_exchange_metadata(row["exchange"]).market.value
            item["symbol"] = f"{row['exchange']}:{row['symbol']}"
            item["unavailable_fields"] = [
                name for name in ("stop_loss", "take_profit", "timeframe")
                if row.get(name) is None
            ]
            items.append(item)
        return self._section(items)

    async def _rankings(self, conn: Any, query: AnalyticsFilter) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        if query.exchange is not None:
            clauses.append("substr(r.canonical_symbol,1,instr(r.canonical_symbol,':')-1)=?")
            values.append(query.exchange)
        if query.market is not None:
            exchanges = [
                exchange.value for exchange in Exchange
                if get_exchange_metadata(exchange).market.value == query.market
            ]
            clauses.append(
                "substr(r.canonical_symbol,1,instr(r.canonical_symbol,':')-1) IN ("
                + ",".join("?" for _ in exchanges) + ")"
            )
            values.extend(exchanges)
        if query.symbol is not None:
            clauses.append("r.canonical_symbol=?")
            values.append(query.symbol)
        created = "(SELECT MIN(d.created_at) FROM beta_scanner_decisions d WHERE d.cycle_id=r.cycle_id)"
        if query.start is not None:
            clauses.append(f"{created}>=?")
            values.append(query.start.isoformat())
        if query.end is not None:
            clauses.append(f"{created}<=?")
            values.append(query.end.isoformat())
        sql = (
            "SELECT r.*, " + created + " AS created_at FROM beta_scanner_rankings r"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, rank, canonical_symbol"
        async with conn.execute(sql, values) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        items = []
        for row in rows:
            exchange = row["canonical_symbol"].split(":", 1)[0]
            items.append(
                {
                    "cycle_id": row["cycle_id"],
                    "market": get_exchange_metadata(exchange).market.value,
                    "exchange": exchange,
                    "symbol": row["canonical_symbol"],
                    "strategy": None,
                    "rank": row["rank"],
                    "score": row["score"],
                    "evidence": json.loads(row["evidence_json"]),
                    "created_at": row["created_at"],
                    "unavailable_fields": ["strategy"] + (["created_at"] if row["created_at"] is None else []),
                }
            )
        section = self._section(items)
        if query.strategy is not None:
            section["unsupported_filters"] = ["strategy"]
        return section

    async def _health(
        self, conn: Any, query: AnalyticsFilter, now: datetime
    ) -> dict[str, Any]:
        quote_clauses, quote_values = self._persisted_filters(
            query, "exchange", "symbol", "timestamp", canonical_column="canonical_symbol"
        )
        quote_sql = "SELECT exchange,canonical_symbol,source,timestamp FROM beta_quotes"
        if quote_clauses:
            quote_sql += " WHERE " + " AND ".join(quote_clauses)
        async with conn.execute(quote_sql, quote_values) as cursor:
            quotes = [dict(row) for row in await cursor.fetchall()]

        providers: dict[tuple[str, str], dict[str, Any]] = {}
        for quote in quotes:
            key = (quote["source"], quote["exchange"])
            current = providers.get(key)
            if current is None or quote["timestamp"] > current["latest_observation"]:
                providers[key] = {
                    "source": quote["source"],
                    "market": get_exchange_metadata(quote["exchange"]).market.value,
                    "exchange": quote["exchange"],
                    "latest_observation": quote["timestamp"],
                }
        provider_items = []
        for item in sorted(providers.values(), key=lambda row: (row["source"], row["exchange"])):
            item["status"] = self._freshness_status(item["latest_observation"], now)
            item["basis"] = "PERSISTED_QUOTE_OBSERVATION"
            provider_items.append(item)
        latest_quote = max((row["timestamp"] for row in quotes), default=None)
        market_data = (
            {"status": "UNAVAILABLE", "latest_observation": None, "reason": "NO_QUOTE_DATA"}
            if latest_quote is None
            else {
                "status": self._freshness_status(latest_quote, now),
                "latest_observation": latest_quote,
                "basis": "PERSISTED_QUOTE_OBSERVATION",
            }
        )

        decision_clauses: list[str] = []
        decision_values: list[Any] = []
        if query.exchange is not None:
            decision_clauses.append("substr(canonical_symbol,1,instr(canonical_symbol,':')-1)=?")
            decision_values.append(query.exchange)
        if query.market is not None:
            exchanges = [
                exchange.value for exchange in Exchange
                if get_exchange_metadata(exchange).market.value == query.market
            ]
            decision_clauses.append(
                "substr(canonical_symbol,1,instr(canonical_symbol,':')-1) IN ("
                + ",".join("?" for _ in exchanges) + ")"
            )
            decision_values.extend(exchanges)
        if query.symbol is not None:
            decision_clauses.append("canonical_symbol=?")
            decision_values.append(query.symbol)
        if query.start is not None:
            decision_clauses.append("created_at>=?")
            decision_values.append(query.start.isoformat())
        if query.end is not None:
            decision_clauses.append("created_at<=?")
            decision_values.append(query.end.isoformat())
        decision_sql = "SELECT code,message,canonical_symbol,created_at FROM beta_scanner_decisions"
        if decision_clauses:
            decision_sql += " WHERE " + " AND ".join(decision_clauses)
        decision_sql += " ORDER BY created_at DESC"
        async with conn.execute(decision_sql, decision_values) as cursor:
            decisions = [dict(row) for row in await cursor.fetchall()]
        errors = [row for row in decisions if row["code"] not in {"QUALIFIED", "DUPLICATE_SIGNAL"}]
        scanner = (
            {"status": "UNAVAILABLE", "last_observation": None, "error_count": 0,
             "latest_errors": [], "reason": "NO_SCANNER_DECISIONS"}
            if not decisions
            else {
                "status": "DEGRADED" if errors else "AVAILABLE",
                "last_observation": decisions[0]["created_at"],
                "error_count": len(errors),
                "latest_errors": errors[:10],
                "basis": "PERSISTED_NON_SUCCESS_DECISIONS",
            }
        )
        return {"market_data": market_data, "scanner": scanner, "providers": provider_items}

    def _backtest_results(self, query: AnalyticsFilter) -> dict[str, Any]:
        runs = [run for run in self._backtests if self._matches_backtest(run, query)]
        if not self._backtests:
            return {"status": "UNAVAILABLE", "items": [], "reason": "NO_BACKTEST_RESULTS"}
        return self._section([self._serialize_backtest(run) for run in runs])

    @staticmethod
    def _matches_backtest(run: BacktestRun, query: AnalyticsFilter) -> bool:
        return not (
            (query.market is not None and run.market != query.market)
            or (query.exchange is not None and run.exchange != query.exchange)
            or (query.symbol is not None and run.symbol != query.symbol)
            or (query.strategy is not None and run.strategy != query.strategy)
            or (query.start is not None and run.ended_at < query.start)
            or (query.end is not None and run.started_at > query.end)
        )

    @staticmethod
    def _serialize_backtest(run: BacktestRun) -> dict[str, Any]:
        result = run.result
        winners = sum(1 for trade in result.trades if trade.net_pnl > 0)
        losers = sum(1 for trade in result.trades if trade.net_pnl < 0)
        summary = {
            "initial_capital": str(result.initial_capital),
            "final_equity": str(result.final_equity),
            "trade_count": len(result.trades),
            "winning_trades": winners,
            "losing_trades": losers,
            "win_rate_pct": str(result.win_rate_pct),
            "profit_factor": str(result.profit_factor) if result.profit_factor is not None else None,
            "net_return_pct": str(result.net_return_pct),
            "max_drawdown_pct": str(result.max_drawdown_pct),
            "transaction_costs": str(result.total_transaction_costs),
            "slippage_costs": str(result.total_slippage_costs),
            "invalid_candle_count": result.invalid_candle_count,
            "open_position_quantity": result.open_position_quantity,
            "unavailable_fields": ["profit_factor"] if result.profit_factor is None else [],
        }
        return {
            "run_id": run.run_id,
            "market": run.market,
            "exchange": run.exchange,
            "symbol": run.symbol,
            "strategy": run.strategy,
            "started_at": run.started_at.isoformat(),
            "ended_at": run.ended_at.isoformat(),
            "summary": summary,
            "equity_curve": [
                {
                    "timestamp": point.timestamp.isoformat(),
                    "equity": str(point.equity),
                    "drawdown_pct": str(point.drawdown_pct),
                }
                for point in result.equity_curve
            ],
            "trades": [
                {
                    "strategy": trade.strategy,
                    "symbol": trade.symbol.canonical,
                    "signal_time": trade.signal_time.isoformat(),
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat(),
                    "quantity": trade.quantity,
                    "entry_price": str(trade.entry_price),
                    "exit_price": str(trade.exit_price),
                    "stop_loss": str(trade.stop_loss),
                    "take_profit": str(trade.take_profit),
                    "exit_reason": trade.exit_reason.value,
                    "gross_pnl": str(trade.gross_pnl),
                    "transaction_costs": str(trade.transaction_costs),
                    "slippage_costs": str(trade.slippage_costs),
                    "net_pnl": str(trade.net_pnl),
                }
                for trade in result.trades
            ],
        }

    def _persisted_filters(
        self,
        query: AnalyticsFilter,
        exchange_column: str,
        symbol_column: str,
        timestamp_column: str,
        *,
        canonical_column: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if query.exchange is not None:
            clauses.append(f"{exchange_column}=?")
            values.append(query.exchange)
        if query.market is not None:
            exchanges = [
                exchange.value for exchange in Exchange
                if get_exchange_metadata(exchange).market.value == query.market
            ]
            clauses.append(f"{exchange_column} IN ({','.join('?' for _ in exchanges)})")
            values.extend(exchanges)
        if query.symbol is not None:
            if canonical_column is not None:
                clauses.append(f"{canonical_column}=?")
                values.append(query.symbol)
            else:
                exchange, separator, symbol = query.symbol.partition(":")
                if separator:
                    clauses.extend((f"{exchange_column}=?", f"{symbol_column}=?"))
                    values.extend((exchange, symbol))
                else:
                    clauses.append(f"{symbol_column}=?")
                    values.append(query.symbol)
        if query.start is not None:
            clauses.append(f"{timestamp_column}>=?")
            values.append(query.start.isoformat())
        if query.end is not None:
            clauses.append(f"{timestamp_column}<=?")
            values.append(query.end.isoformat())
        return clauses, values

    def _freshness_status(self, observed: str, now: datetime) -> str:
        age = now - _timestamp(observed)
        return "FRESH" if timedelta(0) <= age <= self._freshness_window else "STALE"

    @staticmethod
    def _section(items: list[dict[str, Any]]) -> dict[str, Any]:
        section: dict[str, Any] = {"status": "AVAILABLE", "items": items}
        if not items:
            section["reason"] = "NO_DATA"
        return section


__all__ = ["AnalyticsFilter", "AnalyticsService", "BacktestRun"]
