"""QPB portfolio ranking, allocation, risk, and accounting for Paper trading.

QPB consumes only Scanner-qualified canonical signals. It performs no market
discovery and delegates every approved entry to PaperExecutionRouter.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from core.repository.position_repo import PositionRepository
from core.repository.qpb_repo import QPBRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, Signal
from execution.trading.precision_rules import extract_base_coin

logger = get_logger("paper.portfolio.qpb")

CANONICAL_STRATEGIES = tuple(bot.value for bot in BotName)


class QPBValidationError(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class QPBCandidate:
    signal_id: str
    symbol: str
    pair: str
    strategy: str
    strategy_name: str
    entry_price: float
    take_profit: float
    stop_loss: float
    score: float
    confidence: float
    indicators: dict[str, Any]
    signal_reason: str
    generated_at: datetime
    expires_at: datetime
    opportunity_type: str
    market_state: str
    raw_payload: dict[str, Any]
    ranking_score: float = 0.0

    @property
    def risk_reward(self) -> float:
        risk = self.entry_price - self.stop_loss
        reward = self.take_profit - self.entry_price
        return reward / risk if risk > 0 else 0.0

    @property
    def expected_return_pct(self) -> float:
        return ((self.take_profit - self.entry_price) / self.entry_price) * 100.0


class QPBPortfolioService:
    """Restart-safe portfolio gate above the existing Paper execution router."""

    def __init__(
        self,
        *,
        config: AppConfig,
        qpb_repo: QPBRepository,
        position_repo: PositionRepository,
        trade_repo: TradeRepository,
        execution_router: Any,
    ) -> None:
        self.config = config
        self.qpb_repo = qpb_repo
        self.position_repo = position_repo
        self.trade_repo = trade_repo
        self.execution_router = execution_router

    @property
    def total_capital(self) -> float:
        configured = self.config.capital_pool
        return float(configured) if configured and configured > 0 else 10000.0

    async def process_signals(self, signals: list[Signal | dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate, rank, allocate, risk-check, and route one Scanner poll batch."""
        eligible: list[QPBCandidate] = []
        results: list[dict[str, Any]] = []
        batch_now = datetime.now(timezone.utc)
        reserved_total = 0.0
        reserved_by_strategy: dict[str, float] = {}
        reserved_by_coin: dict[str, float] = {}
        reserved_count = 0

        for signal in signals:
            payload = self.scanner_signal_payload(signal)
            signal_id = str(payload.get("signal_id") or payload.get("id") or "")
            if not signal_id:
                signal_id = self._malformed_id(payload)
                payload["signal_id"] = signal_id
                payload["id"] = signal_id

            claimed = await self.qpb_repo.claim(signal_id, payload)
            if not claimed:
                existing_decision = await self.qpb_repo.get(signal_id)
                if existing_decision and existing_decision.get("status") in {"RECEIVED", "RANKED", "ALLOCATED"}:
                    existing_pos = await self.position_repo.get_by_signal_id(signal_id)
                    if existing_pos is not None:
                        await self.qpb_repo.update(
                            signal_id,
                            status="EXECUTED",
                            position_id=existing_pos.id,
                        )
                    else:
                        await self.qpb_repo.update(
                            signal_id,
                            status="REJECTED",
                            reason_code="INTERRUPTED_CYCLE",
                            reason="Previous evaluation cycle interrupted before execution.",
                        )
                results.append(self._result(signal_id, False, "DUPLICATE_SIGNAL", "Signal was already processed by QPB."))
                continue

            existing = await self.position_repo.get_by_signal_id(signal_id)
            if existing is not None:
                await self.qpb_repo.update(
                    signal_id,
                    status="EXECUTED",
                    reason_code="SIGNAL_ALREADY_EXECUTED",
                    reason=f"Existing position {existing.id} owns this signal.",
                    position_id=existing.id,
                )
                results.append(self._result(signal_id, False, "SIGNAL_ALREADY_EXECUTED", "Signal already owns a persisted position."))
                continue

            try:
                candidate = self.validate_signal(payload, now=batch_now)
            except QPBValidationError as exc:
                await self.qpb_repo.update(
                    signal_id,
                    status="REJECTED",
                    reason_code=exc.code,
                    reason=exc.reason,
                    payload=payload,
                )
                results.append(self._result(signal_id, False, exc.code, exc.reason))
                continue

            ranked = QPBCandidate(
                **{
                    **candidate.__dict__,
                    "ranking_score": self.rank_candidate(candidate, now=batch_now),
                }
            )
            await self.qpb_repo.update(
                signal_id,
                status="RANKED",
                ranking_score=ranked.ranking_score,
                payload=payload,
            )
            eligible.append(ranked)

        eligible.sort(key=lambda c: (-c.ranking_score, c.signal_id))
        for candidate in eligible:
            allocation, code, reason = await self.allocate(
                candidate,
                reserved_total=reserved_total,
                reserved_by_strategy=reserved_by_strategy,
                reserved_by_coin=reserved_by_coin,
                reserved_count=reserved_count,
            )
            if allocation <= 0.0:
                await self.qpb_repo.update(
                    candidate.signal_id,
                    status="REJECTED",
                    ranking_score=candidate.ranking_score,
                    allocated_amount=0.0,
                    reason_code=code,
                    reason=reason,
                )
                results.append(self._result(candidate.signal_id, False, code, reason, candidate.ranking_score))
                continue

            payload = dict(candidate.raw_payload)
            payload.update(
                {
                    "id": candidate.signal_id,
                    "signal_id": candidate.signal_id,
                    "mode": BotMode.PAPER.value,
                    "coin": candidate.symbol,
                    "symbol": candidate.symbol,
                    "pair": candidate.pair,
                    "strategy": candidate.strategy,
                    "bot": candidate.strategy,
                    "strategy_name": candidate.strategy_name,
                    "price": candidate.entry_price,
                    "entry_price": candidate.entry_price,
                    "take_profit": candidate.take_profit,
                    "stop_loss": candidate.stop_loss,
                    "score": candidate.score,
                    "confidence": candidate.confidence,
                    "indicators": candidate.indicators,
                    "signal_reason": candidate.signal_reason,
                    "timestamp": candidate.generated_at.isoformat(),
                    "generated_at": candidate.generated_at.isoformat(),
                    "expires_at": candidate.expires_at.isoformat(),
                    "opportunity_type": candidate.opportunity_type,
                    "market_state": candidate.market_state,
                    "amount": allocation,
                    "trade_amount": allocation,
                    "qpb_ranking_score": candidate.ranking_score,
                    "qpb_allocated_amount": allocation,
                    "usdt_inr_rate": float(self.config.usdt_inr_rate),
                }
            )
            await self.qpb_repo.update(
                candidate.signal_id,
                status="ALLOCATED",
                ranking_score=candidate.ranking_score,
                allocated_amount=allocation,
                payload=payload,
            )
            routed = await self.execution_router.handle_signal_event(
                EventType.SIGNAL_GENERATED, payload
            )
            if routed and routed.get("success"):
                position_id = routed.get("position_id")
                await self.qpb_repo.update(
                    candidate.signal_id,
                    status="EXECUTED",
                    ranking_score=candidate.ranking_score,
                    allocated_amount=allocation,
                    reason_code="EXECUTED",
                    reason="Approved by QPB and persisted by Paper execution.",
                    position_id=position_id,
                )
                results.append(
                    {
                        **self._result(candidate.signal_id, True, "EXECUTED", "Paper position opened.", candidate.ranking_score),
                        "allocated_amount": allocation,
                        "position_id": position_id,
                    }
                )
                persisted = await self.position_repo.get_by_signal_id(candidate.signal_id)
                if persisted is None:
                    reserved_total += allocation
                    reserved_count += 1
                    reserved_by_strategy[candidate.strategy] = (
                        reserved_by_strategy.get(candidate.strategy, 0.0) + allocation
                    )
                    candidate_coin = extract_base_coin(candidate.symbol) or candidate.symbol
                    reserved_by_coin[candidate_coin] = (
                        reserved_by_coin.get(candidate_coin, 0.0) + allocation
                    )
            else:
                err = (routed or {}).get("error", "EXECUTION_REJECTED")
                reason = (routed or {}).get("reason") or (routed or {}).get("message") or "Paper execution rejected the allocation."
                await self.qpb_repo.update(
                    candidate.signal_id,
                    status="REJECTED",
                    ranking_score=candidate.ranking_score,
                    allocated_amount=0.0,
                    reason_code=str(err),
                    reason=str(reason),
                )
                results.append(self._result(candidate.signal_id, False, str(err), str(reason), candidate.ranking_score))
        return results

    async def handle_signal_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """EventBus-compatible entry point; Scanner payloads still pass through QPB."""
        results = await self.process_signals([payload])
        return results[0] if results else None

    def validate_signal(self, payload: dict[str, Any], now: datetime | None = None) -> QPBCandidate:
        now = now or datetime.now(timezone.utc)
        signal_id = str(payload.get("signal_id") or payload.get("id") or "").strip()
        symbol = str(payload.get("symbol") or payload.get("coin") or "").strip().upper()
        pair = str(payload.get("pair") or "").strip().upper().replace("_", "/").replace("B-", "")
        strategy = str(payload.get("strategy") or payload.get("bot") or "").strip().upper()
        strategy_name = str(payload.get("strategy_name") or "").strip()
        if not signal_id or signal_id.startswith("malformed:"):
            raise QPBValidationError("MISSING_SIGNAL_ID", "Scanner signal ID is required.")
        if not symbol or not pair:
            raise QPBValidationError("MISSING_SYMBOL", "Scanner symbol and pair are required.")
        if strategy not in CANONICAL_STRATEGIES:
            raise QPBValidationError("INVALID_STRATEGY", f"Unsupported canonical strategy '{strategy}'.")
        if not strategy_name:
            raise QPBValidationError("MISSING_STRATEGY_NAME", "Scanner strategy name is required.")

        entry = self._finite_float(payload.get("entry_price", payload.get("price")), "INVALID_ENTRY_PRICE")
        tp = self._finite_float(payload.get("take_profit"), "INVALID_TP_SL")
        sl = self._finite_float(payload.get("stop_loss"), "INVALID_TP_SL")
        if entry <= 0 or sl <= 0 or tp <= 0 or not (sl < entry < tp):
            raise QPBValidationError("INVALID_TP_SL", "Long signal must satisfy 0 < SL < entry < TP.")

        score = self._finite_float(payload.get("score"), "INVALID_SCORE")
        confidence = self._finite_float(payload.get("confidence", score), "INVALID_CONFIDENCE")
        if not 0 <= score <= 100 or not 0 <= confidence <= 100:
            raise QPBValidationError("INVALID_SCORE", "Signal score and confidence must be within 0..100.")
        indicators = payload.get("indicators")
        reason = payload.get("signal_reason")
        opportunity = str(payload.get("opportunity_type") or "").strip()
        if not isinstance(indicators, dict):
            raise QPBValidationError("INVALID_INDICATORS", "Signal indicators must be an object.")
        if not isinstance(reason, str) or not reason.strip():
            raise QPBValidationError("MISSING_SIGNAL_REASON", "Signal reason is required.")
        if not opportunity:
            raise QPBValidationError("MISSING_OPPORTUNITY_TYPE", "Opportunity type is required.")

        generated = self._datetime(payload.get("generated_at") or payload.get("timestamp"), "INVALID_TIMESTAMP")
        expires = self._datetime(payload.get("expires_at"), "INVALID_TTL")
        if expires <= generated or expires <= now:
            raise QPBValidationError("STALE_SIGNAL", "Signal TTL has expired or is invalid.")
        rr = (tp - entry) / (entry - sl)
        if rr < float(self.config.qpb_min_risk_reward):
            raise QPBValidationError(
                "INVALID_RISK_REWARD",
                f"Risk/reward {rr:.3f} is below minimum {self.config.qpb_min_risk_reward:.3f}.",
            )

        return QPBCandidate(
            signal_id=signal_id,
            symbol=extract_base_coin(symbol) or symbol,
            pair=pair,
            strategy=strategy,
            strategy_name=strategy_name,
            entry_price=entry,
            take_profit=tp,
            stop_loss=sl,
            score=score,
            confidence=confidence,
            indicators=indicators,
            signal_reason=reason.strip(),
            generated_at=generated,
            expires_at=expires,
            opportunity_type=opportunity,
            market_state=str(payload.get("market_state") or ""),
            raw_payload=dict(payload),
        )

    def rank_candidate(self, candidate: QPBCandidate, now: datetime | None = None) -> float:
        """Pure deterministic ranking of already-qualified Scanner opportunities."""
        now = now or datetime.now(timezone.utc)
        lifetime = max(1.0, (candidate.expires_at - candidate.generated_at).total_seconds())
        remaining = max(0.0, (candidate.expires_at - now).total_seconds())
        freshness = min(1.0, remaining / lifetime)
        rr_quality = min(1.0, candidate.risk_reward / 4.0)
        return_quality = min(1.0, candidate.expected_return_pct / 25.0)
        volume = max(0.0, float(candidate.indicators.get("volume_24h", 0.0) or 0.0))
        volume_ratio = max(0.0, float(candidate.indicators.get("volume_ratio", 0.0) or 0.0))
        liquidity = min(1.0, (math.log10(volume + 1.0) / 8.0) + min(volume_ratio, 3.0) / 12.0)
        aligned = bool(candidate.indicators.get("mtf_aligned", False))
        opp_quality = 0.0 if candidate.opportunity_type.lower() in {"watchlist", "avoid"} else 1.0
        rank = (
            candidate.score * 0.35
            + candidate.confidence * 0.10
            + rr_quality * 20.0
            + return_quality * 10.0
            + freshness * 10.0
            + liquidity * 10.0
            + (5.0 if aligned else 0.0)
            + opp_quality * 5.0
        )
        return round(rank, 6)

    async def allocate(
        self,
        candidate: QPBCandidate,
        *,
        reserved_total: float = 0.0,
        reserved_by_strategy: dict[str, float] | None = None,
        reserved_by_coin: dict[str, float] | None = None,
        reserved_count: int = 0,
    ) -> tuple[float, str, str]:
        positions = await self.position_repo.get_open(mode=BotMode.PAPER)
        reserved_by_strategy = reserved_by_strategy or {}
        reserved_by_coin = reserved_by_coin or {}
        total = self.total_capital
        fx = float(self.config.usdt_inr_rate)
        deployed = sum(self._position_notional(p, fx) for p in positions) + reserved_total
        by_strategy: dict[str, float] = {}
        by_coin: dict[str, float] = {}
        strategy_count = 0
        candidate_coin = extract_base_coin(candidate.symbol) or candidate.symbol

        # Cross-process post-exit lock is derived from durable paper trades,
        # never from the scanner's in-memory EventBus.
        cooldown_seconds = int(
            getattr(self.config, "post_exit_cooldown_seconds", 900) or 900
        )
        recent_coin_trades = await self.trade_repo.get_by_coin(
            candidate_coin, limit=1, mode=BotMode.PAPER
        )
        if recent_coin_trades:
            last_exit = recent_coin_trades[0].exit_time
            if last_exit:
                if last_exit.tzinfo is None:
                    last_exit = last_exit.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_exit).total_seconds()
                if 0 <= elapsed < cooldown_seconds:
                    remaining = max(1, int(cooldown_seconds - elapsed))
                    return (
                        0.0,
                        "POST_EXIT_COOLDOWN",
                        f"{candidate_coin} remains locked for {remaining}s after its last exit.",
                    )
        for pos in positions:
            bot = pos.bot.value if hasattr(pos.bot, "value") else str(pos.bot)
            coin = extract_base_coin(pos.coin) or extract_base_coin(pos.pair) or pos.coin
            amount = self._position_notional(pos, fx)
            by_strategy[bot] = by_strategy.get(bot, 0.0) + amount
            by_coin[coin] = by_coin.get(coin, 0.0) + amount
            if bot == candidate.strategy:
                strategy_count += 1

        if any((extract_base_coin(p.coin) or extract_base_coin(p.pair)) == candidate_coin for p in positions) or reserved_by_coin.get(candidate_coin, 0.0) > 0:
            return 0.0, "DUPLICATE_POSITION", f"{candidate_coin} already has an active fleet position."
        if len(positions) + reserved_count >= int(self.config.max_concurrent_positions):
            return 0.0, "MAX_POSITIONS", "Maximum total open positions reached."
        per_strategy_max = int(getattr(self.config, f"max_positions_{candidate.strategy.lower()}", 0) or 0)
        if per_strategy_max and strategy_count >= per_strategy_max:
            return 0.0, "MAX_STRATEGY_POSITIONS", f"{candidate.strategy} position limit reached."

        util_cap = total * float(self.config.qpb_max_portfolio_utilization_pct) / 100.0
        pos_cap = total * float(self.config.qpb_max_position_allocation_pct) / 100.0
        strat_cap = total * float(self.config.qpb_max_strategy_exposure_pct) / 100.0
        coin_cap = total * float(self.config.qpb_max_coin_exposure_pct) / 100.0
        available = min(
            total - deployed,
            util_cap - deployed,
            pos_cap,
            strat_cap - by_strategy.get(candidate.strategy, 0.0) - reserved_by_strategy.get(candidate.strategy, 0.0),
            coin_cap - by_coin.get(candidate_coin, 0.0) - reserved_by_coin.get(candidate_coin, 0.0),
        )
        if available <= 0:
            return 0.0, "INSUFFICIENT_CAPITAL", "No portfolio headroom remains within configured exposure limits."

        quality = min(1.0, max(0.0, candidate.ranking_score / 100.0))
        minimum = float(self.config.qpb_min_order_size_inr)
        desired = max(
            minimum,
            float(self.config.order_size_inr) * (0.75 + 0.50 * quality),
        )
        allocation = round(min(desired, available), 2)
        if allocation < minimum:
            return 0.0, "MINIMUM_ORDER_SIZE", f"Available allocation ₹{allocation:.2f} is below ₹{minimum:.2f}."
        return allocation, "ALLOCATED", "Allocation fits unified portfolio limits."

    async def get_metrics(self) -> dict[str, Any]:
        positions = await self.position_repo.get_open(mode=BotMode.PAPER)
        trades = await self.trade_repo.get_all(limit=5000, mode=BotMode.PAPER)
        fx = float(self.config.usdt_inr_rate)
        deployed = sum(self._position_notional(p, fx) for p in positions)
        unrealized = sum(float(p.unrealised_pnl or 0.0) * self._pair_fx(p.pair, fx) for p in positions)
        pnls = [float(t.pnl) * self._pair_fx(t.pair, fx) for t in trades]
        realized = sum(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        by_strategy: dict[str, float] = {bot.value: 0.0 for bot in BotName}
        by_coin: dict[str, float] = {}
        for pos in positions:
            bot = pos.bot.value if hasattr(pos.bot, "value") else str(pos.bot)
            coin = extract_base_coin(pos.coin) or extract_base_coin(pos.pair) or pos.coin
            value = self._position_notional(pos, fx)
            by_strategy[bot] = by_strategy.get(bot, 0.0) + value
            by_coin[coin] = by_coin.get(coin, 0.0) + value
        total = self.total_capital
        rejected = await self.qpb_repo.list_recent(status="REJECTED", limit=50)
        rankings = await self.qpb_repo.list_rankings(limit=50)
        return {
            "total_capital": round(total, 2),
            "deployed_capital": round(deployed, 2),
            "available_capital": round(max(0.0, total + realized - deployed), 2),
            "utilization_pct": round((deployed / total * 100.0) if total else 0.0, 2),
            "open_positions": len(positions),
            "closed_trades": len(trades),
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(realized + unrealized, 2),
            "win_rate": round((len(wins) / len(trades) * 100.0) if trades else 0.0, 2),
            "average_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "average_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else (round(sum(wins), 4) if wins else 0.0),
            "exposure_by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
            "exposure_by_coin": {k: round(v, 2) for k, v in sorted(by_coin.items())},
            "allocation_by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
            "risk_limits": {
                "max_total_positions": int(self.config.max_concurrent_positions),
                "max_utilization_pct": float(self.config.qpb_max_portfolio_utilization_pct),
                "max_position_pct": float(self.config.qpb_max_position_allocation_pct),
                "max_strategy_pct": float(self.config.qpb_max_strategy_exposure_pct),
                "max_coin_pct": float(self.config.qpb_max_coin_exposure_pct),
                "minimum_order_size": float(self.config.qpb_min_order_size_inr),
                "minimum_risk_reward": float(self.config.qpb_min_risk_reward),
                "single_coin_lock": bool(self.config.enforce_single_coin_lock),
            },
            "rejected_opportunities": rejected,
            "rankings": rankings,
            "decision_counts": await self.qpb_repo.counts_by_status(),
        }

    @staticmethod
    def scanner_signal_payload(signal: Signal | dict[str, Any]) -> dict[str, Any]:
        if isinstance(signal, dict):
            return dict(signal)
        raw = dict(signal.raw_payload or {})
        strategy = signal.source_bot if signal.source_bot in CANONICAL_STRATEGIES else str(raw.get("strategy") or raw.get("bot") or "")
        payload = dict(raw)
        payload.update(
            {
                "id": signal.id,
                "signal_id": signal.id,
                "mode": BotMode.PAPER.value,
                "coin": signal.coin,
                "symbol": signal.coin,
                "pair": signal.pair,
                "strategy": strategy,
                "bot": strategy,
                "strategy_name": raw.get("strategy_name"),
                "entry_price": raw.get("entry_price", raw.get("price")),
                "price": raw.get("price", raw.get("entry_price")),
                "take_profit": raw.get("take_profit"),
                "stop_loss": raw.get("stop_loss"),
                "score": signal.score,
                "confidence": signal.confidence,
                "indicators": raw.get("indicators"),
                "signal_reason": raw.get("signal_reason"),
                "generated_at": signal.generated_at.isoformat(),
                "timestamp": signal.generated_at.isoformat(),
                "expires_at": signal.expires_at.isoformat(),
                "opportunity_type": signal.opportunity_type.value if hasattr(signal.opportunity_type, "value") else str(signal.opportunity_type),
                "market_state": signal.market_state.value if hasattr(signal.market_state, "value") else str(signal.market_state),
            }
        )
        return payload

    @staticmethod
    def _finite_float(value: Any, code: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise QPBValidationError(code, f"Expected a numeric value, got {value!r}.") from exc
        if not math.isfinite(result):
            raise QPBValidationError(code, "Numeric value must be finite.")
        return result

    @staticmethod
    def _datetime(value: Any, code: str) -> datetime:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str) and value.strip():
            try:
                result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise QPBValidationError(code, f"Invalid timestamp {value!r}.") from exc
        else:
            raise QPBValidationError(code, "Timestamp is required.")
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)

    @staticmethod
    def _pair_fx(pair: str, fx: float) -> float:
        upper = str(pair).upper()
        return fx if upper.endswith("USDT") or upper.endswith("USD") else 1.0

    @classmethod
    def _position_notional(cls, position: Any, fx: float) -> float:
        return float(position.entry_price) * float(position.qty) * cls._pair_fx(position.pair, fx)

    @staticmethod
    def _malformed_id(payload: dict[str, Any]) -> str:
        stable = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return "malformed:" + hashlib.sha256(stable).hexdigest()[:24]

    @staticmethod
    def _result(signal_id: str, success: bool, code: str, reason: str, ranking: float | None = None) -> dict[str, Any]:
        return {"signal_id": signal_id, "success": success, "code": code, "reason": reason, "ranking_score": ranking}
