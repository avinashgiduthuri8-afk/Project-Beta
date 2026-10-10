"""Phase 4 QPB portfolio architecture tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.bus.event_bus import EventBus
from core.config import AppConfig
from core.repository.db import Database
from core.repository.position_repo import PositionRepository
from core.repository.qpb_repo import QPBRepository
from core.repository.trade_repo import TradeRepository
from core.types import BotMode, BotName, ExitReason, MarketState, OppType, Priority, RiskLevel, Signal
from execution.auto_trader import AutoTradeRouter
from execution.position_manager import PositionManager
from execution.risk.capital_guard import CapitalGuard
from paper.execution.router import PaperExecutionRouter
from paper.portfolio import QPBPortfolioService, QPBValidationError
from telegram.formatters import format_telegram_qpb


class RecordingRouter:
    def __init__(self, succeed: bool = True) -> None:
        self.payloads: list[dict] = []
        self.succeed = succeed

    async def handle_signal_event(self, event_type, payload):
        self.payloads.append(dict(payload))
        if self.succeed:
            return {"success": True, "position_id": f"pos-{payload['signal_id']}"}
        return {"success": False, "error": "EXECUTION_REJECTED", "reason": "router denied"}


@asynccontextmanager
async def qpb_env(tmp_path: Path, **overrides):
    db = Database(str(tmp_path / "qpb.db"))
    await db.open()
    cfg_data = {
        "total_capital_limit": 1000.0,
        "order_size_inr": 200.0,
        "max_concurrent_positions": 4,
        "qpb_max_portfolio_utilization_pct": 80.0,
        "qpb_max_position_allocation_pct": 30.0,
        "qpb_max_strategy_exposure_pct": 60.0,
        "qpb_max_coin_exposure_pct": 40.0,
        "qpb_min_order_size_inr": 200.0,
        "qpb_min_risk_reward": 1.2,
        "usdt_inr_rate": 90.0,
    }
    cfg_data.update(overrides)
    cfg = AppConfig(_env_file=None, **cfg_data)
    positions = PositionRepository(db.connection)
    trades = TradeRepository(db.connection)
    decisions = QPBRepository(db.connection)
    router = RecordingRouter()
    qpb = QPBPortfolioService(
        config=cfg,
        qpb_repo=decisions,
        position_repo=positions,
        trade_repo=trades,
        execution_router=router,
    )
    try:
        yield SimpleNamespace(
            db=db,
            config=cfg,
            positions=positions,
            trades=trades,
            decisions=decisions,
            router=router,
            qpb=qpb,
        )
    finally:
        await db.close()


def signal_payload(
    signal_id: str = "sig-1",
    *,
    coin: str = "BTC",
    strategy: str = "STE",
    score: float = 90.0,
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
    age_seconds: float = 0.0,
) -> dict:
    now = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "id": signal_id,
        "signal_id": signal_id,
        "mode": "PAPER",
        "coin": coin,
        "symbol": coin,
        "pair": f"{coin}/INR",
        "strategy": strategy,
        "bot": strategy,
        "strategy_name": f"{strategy} Strategy",
        "entry_price": entry,
        "price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "score": score,
        "confidence": score,
        "indicators": {
            "volume_24h": 1_000_000.0,
            "volume_ratio": 2.0,
            "mtf_aligned": True,
        },
        "signal_reason": "Scanner-qualified setup",
        "generated_at": now.isoformat(),
        "timestamp": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "opportunity_type": "momentum_trade",
        "market_state": "breakout",
    }


def canonical_signal(strategy: str = "MRB") -> Signal:
    p = signal_payload("canonical-1", coin="SOL", strategy=strategy)
    generated = datetime.fromisoformat(p["generated_at"])
    return Signal(
        id=p["id"], coin=p["coin"], pair=p["pair"],
        market_state=MarketState.BREAKOUT, opportunity_type=OppType.MOMENTUM_TRADE,
        priority=Priority.ELITE, risk_level=RiskLevel.MEDIUM,
        score=95, confidence=94, coin_class="A", mtf_alignment=True,
        generated_at=generated, expires_at=datetime.fromisoformat(p["expires_at"]),
        mode=BotMode.PAPER, source_bot=strategy, raw_payload=p,
    )


@pytest.mark.asyncio
async def test_signal_ingestion_preserves_canonical_strategy_and_all_eight_supported(tmp_path):
    async with qpb_env(
        tmp_path,
        total_capital_limit=10000.0,
        max_concurrent_positions=20,
        qpb_max_portfolio_utilization_pct=100.0,
        qpb_max_strategy_exposure_pct=100.0,
    ) as env:
        payloads = [signal_payload(f"sig-{bot.value}", coin=f"C{i}", strategy=bot.value) for i, bot in enumerate(BotName)]
        results = await env.qpb.process_signals(payloads)
        assert len(results) == 8
        assert all(r["success"] for r in results), results
        assert len(env.router.payloads) == 8
        assert set(p["strategy"] for p in env.router.payloads) == set(bot.value for bot in BotName)
        assert set(p["strategy"] for p in payloads) == set(bot.value for bot in BotName)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p: p.update(entry_price=0), "INVALID_TP_SL"),
        (lambda p: p.update(stop_loss=101), "INVALID_TP_SL"),
        (lambda p: p.update(take_profit=99), "INVALID_TP_SL"),
        (lambda p: p.update(strategy="UNKNOWN"), "INVALID_STRATEGY"),
        (lambda p: p.update(indicators=[]), "INVALID_INDICATORS"),
        (lambda p: p.update(signal_reason=""), "MISSING_SIGNAL_REASON"),
    ],
)
async def test_malformed_signal_rejection_is_persistent(tmp_path, mutation, code):
    async with qpb_env(tmp_path) as env:
        payload = signal_payload()
        mutation(payload)
        result = (await env.qpb.process_signals([payload]))[0]
        assert result["success"] is False
        assert result["code"] == code
        assert (await env.decisions.get("sig-1"))["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_stale_signal_rejection(tmp_path):
    async with qpb_env(tmp_path) as env:
        result = (await env.qpb.process_signals([signal_payload(age_seconds=600)]))[0]
        assert result["code"] == "STALE_SIGNAL"
        assert not env.router.payloads


@pytest.mark.asyncio
async def test_duplicate_signal_rejection_survives_service_restart(tmp_path):
    async with qpb_env(tmp_path) as env:
        payload = signal_payload()
        assert (await env.qpb.process_signals([payload]))[0]["success"]
        restarted = QPBPortfolioService(
            config=env.config, qpb_repo=env.decisions,
            position_repo=env.positions, trade_repo=env.trades,
            execution_router=RecordingRouter(),
        )
        duplicate = (await restarted.process_signals([payload]))[0]
        assert duplicate["code"] == "DUPLICATE_SIGNAL"


def test_portfolio_ranking_is_deterministic_and_quality_sensitive(tmp_path):
    cfg = AppConfig(_env_file=None)
    service = QPBPortfolioService(
        config=cfg, qpb_repo=None, position_repo=None, trade_repo=None, execution_router=None
    )
    now = datetime.now(timezone.utc)
    low = service.validate_signal(signal_payload(score=75), now=now)
    high = service.validate_signal(signal_payload(score=95), now=now)
    assert service.rank_candidate(high, now=now) > service.rank_candidate(low, now=now)
    assert service.rank_candidate(high, now=now) == service.rank_candidate(high, now=now)


@pytest.mark.asyncio
async def test_central_allocation_and_unified_capital_pool(tmp_path):
    async with qpb_env(tmp_path, max_concurrent_positions=10) as env:
        payloads = [signal_payload(f"s{i}", coin=f"COIN{i}", strategy="STE", score=95 - i) for i in range(6)]
        results = await env.qpb.process_signals(payloads)
        allocated = sum(r.get("allocated_amount", 0.0) for r in results)
        assert allocated <= 800.0
        assert any(r["code"] in {"INSUFFICIENT_CAPITAL", "MINIMUM_ORDER_SIZE"} for r in results)


@pytest.mark.asyncio
async def test_minimum_order_size_and_insufficient_capital(tmp_path):
    async with qpb_env(tmp_path, total_capital_limit=210.0, qpb_max_portfolio_utilization_pct=50.0) as env:
        result = (await env.qpb.process_signals([signal_payload()]))[0]
        assert result["code"] == "MINIMUM_ORDER_SIZE"


@pytest.mark.asyncio
async def test_max_positions_constraint(tmp_path):
    async with qpb_env(tmp_path, max_concurrent_positions=1) as env:
        first = signal_payload("first", coin="BTC")
        await env.qpb.process_signals([first])
        # RecordingRouter does not persist, so make the authoritative position explicitly.
        await PositionManager(env.positions, env.trades).open_position(
            bot=BotName.STE, coin="BTC", pair="BTC/INR", entry_price=100,
            qty=2, stop_loss=95, take_profit=110, signal_id="persisted", mode=BotMode.PAPER,
        )
        result = (await env.qpb.process_signals([signal_payload("second", coin="ETH")]))[0]
        assert result["code"] == "MAX_POSITIONS"


@pytest.mark.asyncio
async def test_max_strategy_and_coin_exposure_and_unified_coin_lock(tmp_path):
    async with qpb_env(tmp_path, qpb_max_strategy_exposure_pct=20.0) as env:
        pm = PositionManager(env.positions, env.trades)
        await pm.open_position(
            bot=BotName.STE, coin="BTC", pair="BTC/INR", entry_price=100,
            qty=2, stop_loss=95, take_profit=110, signal_id="existing", mode=BotMode.PAPER,
        )
        duplicate = (await env.qpb.process_signals([signal_payload("dup", coin="BTC", strategy="HDA")]))[0]
        concentrated = (await env.qpb.process_signals([signal_payload("conc", coin="ETH", strategy="STE")]))[0]
        assert duplicate["code"] == "DUPLICATE_POSITION"
        assert concentrated["code"] == "INSUFFICIENT_CAPITAL"


@pytest.mark.asyncio
async def test_invalid_risk_reward_rejected_before_router(tmp_path):
    async with qpb_env(tmp_path) as env:
        result = (await env.qpb.process_signals([signal_payload(stop=90, target=110)]))[0]
        assert result["code"] == "INVALID_RISK_REWARD"
        assert not env.router.payloads


@pytest.mark.asyncio
async def test_real_execution_integration_persistence_and_duplicate_prevention(tmp_path):
    async with qpb_env(tmp_path) as env:
        bus = EventBus()
        pm = PositionManager(env.positions, env.trades, bus)
        router = PaperExecutionRouter(
            bus, AutoTradeRouter(bus, dry_run=True, position_repo=env.positions), pm,
            CapitalGuard(env.config), env.positions,
        )
        env.qpb.execution_router = router
        payload = signal_payload("integrated", coin="ADA", entry=100, stop=95, target=110)
        first = (await env.qpb.process_signals([payload]))[0]
        second = (await env.qpb.process_signals([payload]))[0]
        assert first["success"] is True
        assert second["code"] == "DUPLICATE_SIGNAL"
        assert (await env.positions.get_by_signal_id("integrated")) is not None


@pytest.mark.asyncio
async def test_position_close_releases_capital_coin_lock_and_reconciles_pnl(tmp_path):
    async with qpb_env(tmp_path) as env:
        bus = EventBus()
        pm = PositionManager(env.positions, env.trades, bus)
        pos = await pm.open_position(
            bot=BotName.HDA, coin="XRP", pair="XRP/INR", entry_price=100,
            qty=2, stop_loss=95, take_profit=110, signal_id="open", mode=BotMode.PAPER,
        )
        before = await env.qpb.get_metrics()
        _, trade = await pm.close_position(pos.id, 110, ExitReason.TAKE_PROFIT)
        after = await env.qpb.get_metrics()
        assert before["deployed_capital"] == 200.0
        assert after["deployed_capital"] == 0.0
        assert after["realized_pnl"] == trade.pnl
        assert after["closed_trades"] == 1
        allocation, code, _ = await env.qpb.allocate(env.qpb.validate_signal(signal_payload("reopen", coin="XRP")))
        assert allocation == 0.0
        assert code == "POST_EXIT_COOLDOWN"


@pytest.mark.asyncio
async def test_unrealized_and_full_portfolio_analytics(tmp_path):
    async with qpb_env(tmp_path) as env:
        pm = PositionManager(env.positions, env.trades)
        pos = await pm.open_position(
            bot=BotName.PPA, coin="DOT", pair="DOT/INR", entry_price=100,
            qty=2, stop_loss=95, take_profit=110, signal_id="analytics", mode=BotMode.PAPER,
        )
        await env.positions.update_price(pos.id, 105, 10)
        metrics = await env.qpb.get_metrics()
        assert metrics["unrealized_pnl"] == 10.0
        assert metrics["total_pnl"] == 10.0
        assert metrics["exposure_by_strategy"]["PPA"] == 200.0
        assert metrics["exposure_by_coin"]["DOT"] == 200.0
        assert set(metrics["exposure_by_strategy"]) == set(bot.value for bot in BotName)


def test_telegram_portfolio_exposure_allocation_and_risk_formatting():
    metrics = {
        "total_capital": 1000, "deployed_capital": 200, "available_capital": 800,
        "utilization_pct": 20, "open_positions": 1, "closed_trades": 2,
        "realized_pnl": 10, "unrealized_pnl": 5, "total_pnl": 15,
        "win_rate": 50, "profit_factor": 2, "rejected_opportunities": [],
        "exposure_by_strategy": {"STE": 200}, "exposure_by_coin": {"BTC": 200},
        "allocation_by_strategy": {"STE": 200},
        "risk_limits": {"max_total_positions": 4, "max_utilization_pct": 80,
                        "max_position_pct": 30, "max_strategy_pct": 60,
                        "max_coin_pct": 40, "minimum_order_size": 200,
                        "minimum_risk_reward": 1.2, "single_coin_lock": True},
    }
    assert "QPB PAPER PORTFOLIO" in format_telegram_qpb(metrics)
    assert "BTC" in format_telegram_qpb(metrics, "exposure")
    assert "STE" in format_telegram_qpb(metrics, "allocation")
    assert "Coin lock" in format_telegram_qpb(metrics, "risk")


def test_qpb_has_no_market_discovery_dependencies():
    import inspect
    from paper.portfolio import qpb as module

    source = inspect.getsource(module)
    assert "api.coindcx.com" not in source
    assert "market/public_client" not in source
    assert "scan" not in {name.lower() for name in dir(QPBPortfolioService)}
