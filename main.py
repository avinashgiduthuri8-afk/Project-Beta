"""Master Orchestrator and Indian Market Trading Bot Lifecycle Supervisor."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime

from config.config_loader import load_config, AppConfig
from core.enums import MarketSession, OrderSide, ProductType, OrderType
from core.models import Tick, OrderRequest
from brokers import get_broker, PaperBroker
from oms.execution_router import ExecutionRouter
from oms.order_manager import OrderManager
from oms.order_book_syncer import OrderBookSyncer
from risk.market_clock import MarketClock
from risk.risk_engine import RiskEngine
from risk.rate_limiter import RateLimiter
from risk.position_sizer import PositionSizer
from data.event_bus import EventBus
from data.candle_builder import CandleBuilder
from data.ticker import WebSocketTicker
from storage.database import Database
from storage.journal import Journal
from notifications.telegram import TelegramNotifier
from notifications.discord import DiscordNotifier
from strategies.sample_vwap_momentum import VWAPMomentumStrategy

# Configure standard logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ProjectBeta.Main")


class ExecutionBot:
    """Master Supervisor managing daily market lifecycle, RMS, and event loops."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.is_running = False

        # 1. Infrastructure & Storage
        self.event_bus = EventBus()
        self.db = Database(config.storage.database_path)
        self.journal = Journal(config.storage.journal_csv_path)

        # 2. Broker & Adapters
        self.broker = get_broker(config)

        # 3. OMS & Execution Router
        self.order_manager = OrderManager()
        self.execution_router = ExecutionRouter(self.broker)
        self.order_syncer = OrderBookSyncer(self.broker, self.order_manager)

        # 4. RMS & Market Clock
        self.market_clock = MarketClock(
            timezone_str=config.trading.timezone,
            pre_open_str=config.market_hours.pre_open_time,
            market_open_str=config.market_hours.market_open_time,
            square_off_str=config.market_hours.auto_square_off_time,
            market_close_str=config.market_hours.market_close_time,
        )
        self.rate_limiter = RateLimiter(rate=float(config.risk.max_orders_per_second))
        self.risk_engine = RiskEngine(
            max_daily_loss=config.risk.max_daily_loss,
            max_open_positions=config.risk.max_open_positions,
            rate_limiter=self.rate_limiter,
            market_clock=self.market_clock,
        )

        # 5. Data Feed & Resampler
        self.candle_builder = CandleBuilder(
            timeframe_minutes=config.strategy.timeframe_minutes,
            on_candle_close=self._on_candle_close,
        )
        tokens = [s.token for s in config.trading.symbols]
        self.ticker = WebSocketTicker(self.event_bus, tokens=tokens)

        # 6. Notifications
        self.telegram = TelegramNotifier(enabled=config.notifications.telegram_enabled)
        self.discord = DiscordNotifier(enabled=config.notifications.discord_enabled)

        # 7. Strategy
        self.strategy = VWAPMomentumStrategy(
            router=self.execution_router,
            order_manager=self.order_manager,
            stop_loss_pct=config.strategy.stop_loss_pct,
            target_rr=config.strategy.target_rr_ratio,
        )

        # Register event handlers
        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self) -> None:
        self.event_bus.subscribe("market.tick", self._handle_market_tick)
        self.order_manager.add_listener(self._handle_order_update)

    def _handle_market_tick(self, tick: Tick) -> None:
        if isinstance(self.broker, PaperBroker):
            self.broker.set_market_price(tick.symbol, tick.ltp)
        self.candle_builder.process_tick(tick)
        self.strategy.on_tick(tick)

    def _on_candle_close(self, candle) -> None:
        self.strategy.on_candle(candle)

    def _handle_order_update(self, order) -> None:
        self.db.save_order(order)
        self.strategy.on_order_update(order)
        if order.status.value in ("COMPLETE", "REJECTED", "CANCELLED"):
            alert_msg = f"Order {order.symbol} ({order.side.value} {order.quantity}) is {order.status.value} @ ₹{order.average_price:.2f}"
            self.telegram.send_alert("Order Update", alert_msg)
            self.discord.send_alert("Order Update", alert_msg)

    def start(self) -> None:
        """Run daily lifecycle supervisor."""
        logger.info("Initializing Project-Beta Execution Bot...")
        if not self.broker.authenticate():
            logger.error("Broker authentication failed. Halting startup.")
            return

        funds = self.broker.get_funds()
        logger.info(f"Connected to Broker. Available Margin: ₹{funds.available_margin:,.2f}")

        self.is_running = True
        self.ticker.start()

        logger.info("Bot is active and listening for market events...")

    def stop(self) -> None:
        """Gracefully shut down all components."""
        logger.info("Shutting down Project-Beta...")
        self.is_running = False
        self.ticker.stop()

        # Save daily snapshot
        funds = self.broker.get_funds()
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.db.save_daily_snapshot(
            date_str=today_str,
            realized_pnl=funds.realized_pnl,
            unrealized_pnl=funds.unrealized_pnl,
            total_trades=len(self.db._get_connection().cursor().execute("SELECT trade_id FROM trades").fetchall()),
        )
        logger.info(f"Daily P&L Snapshot saved: Realized=₹{funds.realized_pnl:,.2f}, Unrealized=₹{funds.unrealized_pnl:,.2f}")
        logger.info("Project-Beta execution completed cleanly.")

    def auto_square_off_intraday(self) -> None:
        """Square off open MIS positions at 15:15 IST."""
        positions = self.broker.get_positions()
        logger.info(f"Triggering 15:15 IST Square-off for {len(positions)} positions...")
        for pos in positions:
            if pos.product_type == ProductType.MIS and pos.quantity != 0:
                side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
                close_qty = abs(pos.quantity)
                logger.info(f"Auto-squaring off MIS position: {side.value} {close_qty}x {pos.symbol}")
                self.strategy.place_order(
                    symbol=pos.symbol,
                    side=side,
                    quantity=close_qty,
                    order_type=OrderType.MARKET,
                    product_type=ProductType.MIS,
                    exchange=pos.exchange,
                )


def parse_args():
    parser = argparse.ArgumentParser(description="Project-Beta: Indian Stock Market Algorithmic Execution Bot")
    parser.add_argument("--mode", type=str, default="paper", choices=["paper", "live"], help="Trading mode")
    parser.add_argument("--broker", type=str, default="paper", choices=["paper", "zerodha", "angel_one", "dhan"], help="Broker adapter")
    parser.add_argument("--config", type=str, default=None, help="Path to custom settings.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Perform sanity check and exit immediately")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config(args.config)
    config.trading.mode = args.mode
    config.trading.broker = args.broker

    bot = ExecutionBot(config)

    if args.dry_run:
        logger.info("🧪 DRY-RUN MODE: Verifying bot initialization, broker adapter, and risk engine...")
        auth_ok = bot.broker.authenticate()
        funds = bot.broker.get_funds()
        session = bot.market_clock.get_current_session()
        logger.info(f"• Broker: {bot.broker.__class__.__name__} (Authenticated: {auth_ok})")
        logger.info(f"• Funds: Available Margin = ₹{funds.available_margin:,.2f}")
        logger.info(f"• Market Session: {session.value}")
        logger.info("✅ Dry-run completed successfully.")
        return 0

    def sig_handler(signum, frame):
        logger.info(f"Signal {signum} received. Stopping bot...")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    bot.start()

    try:
        while bot.is_running:
            time.sleep(1)
            # Sync orders
            bot.order_syncer.sync()

            # Check for 15:15 IST square-off
            if bot.market_clock.is_auto_square_off_time():
                bot.auto_square_off_intraday()
                time.sleep(60)  # Wait after square-off
    except KeyboardInterrupt:
        pass
    finally:
        bot.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
