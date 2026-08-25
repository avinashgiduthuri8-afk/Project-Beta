"""
Project-Beta: Master Orchestrator & Execution Supervisor for Indian Stocks (NSE/BSE).
Manages authentication, market clock scheduling, OMS, RMS, live streaming, and auto-square-off.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from config.config_loader import BotSettings, load_settings
from core.enums import MarketSession, OrderSide, OrderStatus, OrderType, ProductType
from core.models import Candle, Order, OrderRequest, Tick, Trade
from brokers import get_broker
from data.event_bus import EventBus
from data.candle_builder import CandleBuilder
from data.ticker import WebSocketTicker
from oms.order_manager import OrderManager
from oms.order_book_syncer import OrderBookSyncer
from risk.risk_engine import RiskEngine
from risk.market_clock import MarketClock
from storage.database import Database
from storage.journal import TradeJournal
from notifications.telegram import TelegramNotifier
from notifications.discord import DiscordNotifier
from strategies.sample_vwap_momentum import VWAPMomentumStrategy

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ProjectBeta.Main")


class ExecutionBotOrchestrator:
    """
    Main supervisor managing the full lifecycle of the execution bot across Indian market hours.
    """

    def __init__(self, settings: Optional[BotSettings] = None) -> None:
        self.settings = settings or load_settings()
        self.running = False
        self.market_clock = MarketClock(
            pre_market_start=self.settings.market_clock.pre_market_start,
            market_open=self.settings.market_clock.market_open,
            auto_square_off=self.settings.market_clock.auto_square_off,
            market_close=self.settings.market_clock.market_close,
        )

        # Storage & Notifiers
        self.db = Database(self.settings.storage.db_path)
        self.journal = TradeJournal(self.settings.storage.journal_csv_path)
        self.tg_notifier = TelegramNotifier(self.settings.notifications.telegram)
        self.dc_notifier = DiscordNotifier(self.settings.notifications.discord)

        # Broker
        self.broker = get_broker(self.settings)

        # Risk & OMS
        self.risk_engine = RiskEngine(self.settings)
        self.order_manager = OrderManager(
            broker=self.broker,
            settings=self.settings,
            on_order_update=self._handle_order_update,
        )
        self.order_syncer = OrderBookSyncer(
            broker=self.broker,
            order_manager=self.order_manager,
            interval_sec=self.settings.execution.order_book_sync_interval_sec,
        )

        # Data Pipeline
        self.event_bus = EventBus()
        self.candle_builder = CandleBuilder(
            timeframe_minutes=1,
            on_candle_closed=self._handle_candle_closed,
            on_candle_update=self._handle_candle_update,
        )
        self.ticker = WebSocketTicker(
            settings=self.settings,
            event_bus=self.event_bus,
            symbols=self.settings.symbols,
        )

        # Strategy
        symbols_list = [s.symbol for s in self.settings.symbols]
        self.strategy = VWAPMomentumStrategy(
            symbols=symbols_list,
            submit_order_fn=self.submit_strategy_order,
            quantity_per_trade=10,
        )

        self._squared_off_today = False
        self._reported_today = False

        # Setup event bus subscribers
        self.event_bus.subscribe("tick", self._handle_tick)

    def _handle_tick(self, tick: Tick) -> None:
        """Process incoming tick."""
        self.candle_builder.process_tick(tick)
        self.strategy.on_tick(tick)

    def _handle_candle_closed(self, candle: Candle) -> None:
        """Process closed candle."""
        self.strategy.on_candle(candle)

    def _handle_candle_update(self, candle: Candle) -> None:
        pass

    def _handle_order_update(self, order: Order) -> None:
        """Callback on order status transition."""
        self.db.save_order(order)
        self.tg_notifier.send_order_alert(order)
        self.dc_notifier.send_order_alert(order)

        if order.status == OrderStatus.COMPLETE:
            trade = Trade(
                trade_id=f"TRD_{order.order_id}",
                order_id=order.order_id,
                exchange_order_id=order.exchange_order_id,
                symbol=order.symbol,
                exchange=order.exchange,
                side=order.side,
                product=order.product,
                price=order.average_price or order.price or 0.0,
                quantity=order.filled_quantity or order.quantity,
                timestamp=datetime.now(timezone.utc),
            )
            self.db.save_trade(trade)
            self.journal.log_trade(trade)
            self.risk_engine.record_trade(trade)

        self.strategy.on_order_update(order)

    def submit_strategy_order(self, request: OrderRequest) -> Optional[Order]:
        """
        Risk-guarded order submission entrypoint for strategies.
        """
        funds = self.broker.get_funds()

        # Step 1: RMS Pre-trade Evaluation
        risk_result = self.risk_engine.evaluate_order(request, funds)
        if not risk_result.allowed:
            logger.warning(f"🚫 Order blocked by RMS: {risk_result.reason}")
            self.tg_notifier.send_rms_alert(f"Order for {request.symbol} blocked: {risk_result.reason}")
            self.dc_notifier.send_rms_alert(f"Order for {request.symbol} blocked: {risk_result.reason}")
            return None

        # Step 2: Route via OMS
        symbol_cfg = next((s for s in self.settings.symbols if s.symbol == request.symbol), None)
        lot_size = symbol_cfg.lot_size if symbol_cfg else 1
        tick_size = symbol_cfg.tick_size if symbol_cfg else 0.05

        order = self.order_manager.submit_order(request, lot_size=lot_size, tick_size=tick_size)
        return order

    def auto_square_off_intraday(self) -> None:
        """
        Square off all open MIS positions and cancel pending MIS orders at 15:15 IST.
        """
        if self._squared_off_today:
            return

        logger.warning("⏰ 15:15 IST REACHED — INITIATING AUTOMATED MIS INTRADAY AUTO-SQUARE-OFF...")
        self.tg_notifier.send_message("⏰ *15:15 IST Auto-Square-Off Activated.* Squaring off open MIS positions...")
        self.dc_notifier.send_message("⏰ **15:15 IST Auto-Square-Off Activated.** Squaring off open MIS positions...")

        # 1. Cancel pending open orders
        cancelled = self.order_manager.cancel_all_open_orders()
        logger.info(f"Cancelled {cancelled} open pending orders before square-off.")

        # 2. Close open positions
        positions = self.broker.get_positions()
        for pos in positions:
            if pos.product == ProductType.MIS and pos.quantity != 0:
                side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
                close_qty = abs(pos.quantity)
                logger.info(f"Squaring off {pos.symbol} [{pos.product.value}]: {side.value} {close_qty}")

                req = OrderRequest(
                    symbol=pos.symbol,
                    exchange=pos.exchange,
                    side=side,
                    order_type=OrderType.MARKET,
                    product=ProductType.MIS,
                    quantity=close_qty,
                    tag="AutoSquareOff_1515",
                )
                self.broker.place_order(req)

        self._squared_off_today = True
        logger.info("Auto-square-off routine completed.")

    def generate_daily_report(self) -> None:
        """Generate and send end-of-day execution summary report."""
        if self._reported_today:
            return

        trades = self.db.get_trades(limit=100)
        summary = self.journal.generate_daily_summary(trades)
        logger.info(f"\n{summary}")
        self.tg_notifier.send_message(summary)
        self.dc_notifier.send_message(summary)
        self._reported_today = True

    def start(self) -> None:
        """Start orchestrator and run daily market lifecycle."""
        self.running = True
        logger.info("=" * 60)
        logger.info(f"🚀 STARTING {self.settings.app.name.upper()} EXECUTION BOT")
        logger.info(f"• Mode: {self.settings.env.trading_mode} | Broker: {self.settings.env.active_broker}")
        logger.info(f"• Timezone: {self.settings.app.timezone} | Max Loss: ₹{self.settings.risk.max_daily_loss_inr:,.2f}")
        logger.info("=" * 60)

        # 1. Authenticate Broker
        if not self.broker.authenticate():
            logger.error("Broker authentication failed. Halting startup.")
            return

        # 2. Start Background Workers
        self.ticker.start()
        self.order_syncer.start()

        # 3. Main Market Loop
        try:
            while self.running:
                session = self.market_clock.get_session()
                ist_now_str = MarketClock.get_ist_now().strftime("%H:%M:%S")

                if session == MarketSession.PRE_OPEN:
                    logger.info(f"[{ist_now_str} IST] Market Pre-Open session. Awaiting 09:15 open...")
                elif session == MarketSession.TRADING:
                    # Active Trading
                    self._squared_off_today = False
                    self._reported_today = False
                elif session == MarketSession.AUTO_SQUARE_OFF:
                    # 15:15 - 15:30 IST
                    self.auto_square_off_intraday()
                elif session == MarketSession.POST_CLOSE:
                    # 15:30+ IST
                    self.generate_daily_report()

                time.sleep(2.0)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Graceful shutdown handler for SIGINT/SIGTERM."""
        logger.info("Initiating graceful shutdown...")
        self.running = False
        self.ticker.stop()
        self.order_syncer.stop()
        logger.info("Project-Beta execution bot safely stopped.")


import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project-Beta: Indian Equities & Derivatives Algorithmic Execution Bot")
    parser.add_argument("--mode", choices=["PAPER", "LIVE", "paper", "live"], default=None, help="Execution mode (PAPER or LIVE)")
    parser.add_argument("--broker", choices=["PAPER", "ZERODHA", "ANGEL_ONE", "DHAN", "paper", "zerodha", "angel_one", "dhan"], default=None, help="Target broker")
    parser.add_argument("--config", type=str, default=None, help="Path to custom settings.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Perform startup verification and exit cleanly")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(args.config)

    if args.mode:
        settings.env.trading_mode = args.mode.upper()
    if args.broker:
        settings.env.active_broker = args.broker.upper()

    bot = ExecutionBotOrchestrator(settings=settings)

    if args.dry_run:
        logger.info("🧪 DRY-RUN MODE: Verifying bot initialization, broker adapter, and risk engine...")
        authenticated = bot.broker.authenticate()
        funds = bot.broker.get_funds()
        session = bot.market_clock.get_session()
        logger.info(f"• Broker: {bot.broker.__class__.__name__} (Authenticated: {authenticated})")
        logger.info(f"• Funds: Available Margin = ₹{funds.available_margin:,.2f}")
        logger.info(f"• Market Session: {session.value}")
        logger.info("✅ Dry-run completed successfully.")
        return

    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}. Stopping...")
        bot.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    bot.start()


if __name__ == "__main__":
    main()

