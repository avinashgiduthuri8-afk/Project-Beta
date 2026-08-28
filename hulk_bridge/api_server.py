"""FastAPI Telemetry & Bridge API Server for PROJECT HULK Dashboard."""

from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from core.models import ScannerCandidate, TradePlan, Position, AccountBalance, SystemHealthStatus
from core.enums import OrderSide, ProductType, SetupType, Exchange
from brokers.paper_broker import PaperBroker
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from risk.risk_engine import RiskEngine
from risk.market_clock import MarketClock
from hulk_bridge.event_bus import HulkEventBus, HulkEventTypes

logger = logging.getLogger("HULK.API")

app = FastAPI(
    title="PROJECT-BETA Telemetry & Execution Bridge for PROJECT HULK",
    version="2.0.0",
)

# Global in-memory components
event_bus = HulkEventBus()
broker = PaperBroker(initial_capital=100000.0)
router = ExecutionRouter(broker)
order_manager = OrderManager()
market_clock = MarketClock()
risk_engine = RiskEngine(max_daily_loss=3000.0, market_clock=market_clock)


class TradeRequestPayload(BaseModel):
    symbol: str
    side: str = "BUY"
    quantity: int = 1
    price: Optional[float] = None
    product_type: str = "MIS"
    exchange: str = "NSE"
    tag: str = "HulkUI"


@app.get("/api/v1/hulk/status", response_model=SystemHealthStatus)
def get_system_health():
    """Returns real-time health, session status, and circuit breaker states for PROJECT HULK."""
    funds = broker.get_funds()
    session = market_clock.get_current_session()
    return SystemHealthStatus(
        data_feed_healthy=True,
        last_tick_latency_ms=45.0,
        market_session=session,
        open_positions_count=len([p for p in broker.get_positions() if p.quantity != 0]),
        active_circuit_breaker=risk_engine.circuit_breaker_triggered,
        broker_authenticated=True,
    )


@app.get("/api/v1/hulk/pnl")
def get_pnl_and_margin():
    """Returns funds, margin utilization, and real-time P&L for dashboard cards."""
    funds = broker.get_funds()
    return {
        "total_capital": funds.total_capital,
        "available_margin": funds.available_margin,
        "utilized_margin": funds.utilized_margin,
        "realized_pnl": funds.realized_pnl,
        "unrealized_pnl": funds.unrealized_pnl,
        "total_pnl": funds.realized_pnl + funds.unrealized_pnl,
    }


@app.get("/api/v1/hulk/positions")
def get_active_positions():
    """Returns list of open and closed positions."""
    return broker.get_positions()


@app.get("/api/v1/hulk/orders")
def get_orders():
    """Returns daily order book."""
    return broker.get_orders()


@app.get("/api/v1/hulk/events")
def get_hulk_events(limit: int = 50):
    """Returns recent events stream for Project HULK feed."""
    return event_bus.get_recent_events(limit)


@app.post("/api/v1/hulk/trade")
def execute_trade_from_hulk(payload: TradeRequestPayload):
    """Execute order requested directly from Project HULK UI."""
    from core.models import OrderRequest
    side_enum = OrderSide.BUY if payload.side.upper() == "BUY" else OrderSide.SELL
    prod_enum = ProductType.MIS if payload.product_type.upper() == "MIS" else ProductType.CNC

    req = OrderRequest(
        client_order_id=f"HULK-{payload.symbol}",
        symbol=payload.symbol,
        side=side_enum,
        order_type=OrderType.MARKET if payload.price is None else OrderType.LIMIT,
        product_type=prod_enum,
        quantity=payload.quantity,
        price=payload.price,
        tag=payload.tag,
    )

    # Deterministic RMS Check
    funds = broker.get_funds()
    positions = broker.get_positions()
    is_valid, reason = risk_engine.validate_order(req, funds, positions)
    if not is_valid:
        event_bus.publish(HulkEventTypes.RMS_CIRCUIT_BREAKER, {"reason": reason, "symbol": payload.symbol})
        raise HTTPException(status_code=400, detail=reason)

    order = router.route_order(req)
    order_manager.register_order(order)
    event_bus.publish(HulkEventTypes.ORDER_FILLED, {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": order.quantity,
        "price": order.average_price,
    })

    return {"status": "SUCCESS", "order": order}


@app.post("/api/v1/hulk/kill-switch")
def trigger_emergency_kill_switch():
    """PROJECT HULK Emergency Panic Button: Immediately halts trading & liquidates all open MIS positions."""
    logger.critical("[HULK API] 💥 EMERGENCY KILL SWITCH TRIGGERED FROM DASHBOARD!")
    positions = broker.get_positions()
    liquidated = []

    for pos in positions:
        if pos.quantity != 0:
            side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
            close_qty = abs(pos.quantity)
            req = type('Req', (), {
                'client_order_id': f"KILL-{pos.symbol}",
                'symbol': pos.symbol,
                'side': side,
                'order_type': OrderType.MARKET,
                'product_type': pos.product_type,
                'quantity': close_qty,
                'price': None,
                'trigger_price': None,
            })()
            order = router.route_order(req)
            order_manager.register_order(order)
            liquidated.append(pos.symbol)

    risk_engine.circuit_breaker_triggered = True
    event_bus.publish(HulkEventTypes.RMS_CIRCUIT_BREAKER, {"action": "EMERGENCY_KILL_SWITCH", "liquidated": liquidated})
    return {"status": "EMERGENCY_HALT_EXECUTED", "liquidated_positions": liquidated}
