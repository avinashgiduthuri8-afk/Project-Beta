"""Project-Beta Single Telegram Bot Dispatcher.

Handles asynchronous notification delivery for scanner signals, paper trade fills,
exit PnL, and portfolio snapshots to a single Telegram bot and chat.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from core.logging import get_logger

logger = get_logger("telegram.client")


class TelegramClient:
    """Async Telegram notification client for Project-Beta."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 8.0,
        resolve_default_credentials: bool = True,
    ) -> None:
        if resolve_default_credentials and (bot_token is None or chat_id is None):
            try:
                from core.config import get_config
                cfg = get_config()
                bot_token = bot_token or getattr(cfg, "telegram_bot_token", None) or os.getenv("TELEGRAM_BOT_TOKEN")
                chat_id = chat_id or getattr(cfg, "telegram_chat_id", None) or os.getenv("TELEGRAM_CHAT_ID")
            except Exception as e:
                logger.debug("Failed to resolve Telegram credentials from config: %s", e)
                bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
                chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        self.bot_token = str(bot_token).strip() if bot_token else None
        self.chat_id = str(chat_id).strip() if chat_id else None
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._last_send = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_preview: bool = True,
    ) -> bool:
        """Send a message to the configured Telegram chat."""
        if not self.is_configured:
            logger.debug("Telegram notification skipped: Bot token or Chat ID not configured")
            return False

        # Throttling to respect Telegram API rate limits (max 20 msgs/min per chat)
        now = time.monotonic()
        elapsed = now - self._last_send
        if elapsed < 0.2:
            await asyncio.sleep(0.2 - elapsed)
        self._last_send = time.monotonic()

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        client = await self._get_client()
        for attempt in range(2):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200 and resp.json().get("ok"):
                    return True
                logger.warning(
                    "Telegram API rejected message (status %d): %s",
                    resp.status_code,
                    resp.text[:200],
                )
            except Exception as e:
                logger.warning("Telegram dispatch failed (attempt %d/2): %s", attempt + 1, e)
                await asyncio.sleep(1.0)
        return False

    async def notify_runtime_start(
        self,
        capital: float = 15000.0,
        price_ceiling: float = 250.0,
        watchlist_count: int = 16,
    ) -> bool:
        msg = (
            "🚀 <b>Project-Beta Indian Equities Engine Started</b>\n\n"
            f"💰 <b>Capital:</b> ₹{capital:,.2f}\n"
            f"🎯 <b>Price Range:</b> ₹0 – ₹{price_ceiling:.0f}\n"
            f"📋 <b>Watchlist:</b> {watchlist_count} dynamic NSE stocks\n"
            "⚡ <b>Active Strategies:</b> STE, HDA, VCP, BBS, NR7\n"
            "📰 <b>30-Day News Guard:</b> ACTIVE\n"
            "🕒 <b>Holding Horizon:</b> 1 Week – 1 Month (CNC Delivery)\n"
            "<i>Single Server Runtime Online</i>"
        )
        return await self.send_message(msg)

    async def notify_scanner_signal(
        self,
        symbol: str,
        strategy: str,
        price: float,
        score: int,
        news_status: str = "SAFE",
        headline: str = "",
    ) -> bool:
        headline_block = f"\n📰 <i>Headline: \"{headline[:75]}...\"</i>" if headline else ""
        msg = (
            f"🎯 <b>NEW SWING SIGNAL QUALIFIED</b>\n\n"
            f"📈 <b>Stock:</b> <code>{symbol}</code>\n"
            f"💵 <b>Current Price:</b> ₹{price:.2f}\n"
            f"⚡ <b>Strategy:</b> <b>{strategy.upper()}</b> (Score: {score}/100)\n"
            f"🛡️ <b>30d News Sentiment:</b> {news_status}"
            f"{headline_block}\n\n"
            f"<i>Handing over to Paper Execution Engine...</i>"
        )
        return await self.send_message(msg)

    async def notify_paper_buy(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        total_cost: float,
        target_price: float,
        sl_price: float,
        strategy: str,
    ) -> bool:
        target_pct = ((target_price - entry_price) / entry_price) * 100
        sl_pct = ((entry_price - sl_price) / entry_price) * 100
        msg = (
            f"🟢 <b>PAPER ORDER EXECUTED (BUY)</b>\n\n"
            f"📌 <b>Stock:</b> <code>{symbol}</code>\n"
            f"🔢 <b>Quantity:</b> {qty} whole shares (CNC Delivery)\n"
            f"💵 <b>Fill Price:</b> ₹{entry_price:.2f}\n"
            f"💳 <b>Total Invested:</b> ₹{total_cost:,.2f}\n"
            f"🎯 <b>Target:</b> ₹{target_price:.2f} (+{target_pct:.1f}%)\n"
            f"🛑 <b>Stop Loss:</b> ₹{sl_price:.2f} (-{sl_pct:.1f}%)\n"
            f"⚡ <b>Strategy:</b> {strategy}\n\n"
            f"<i>Holding for 1-4 week swing expansion</i>"
        )
        return await self.send_message(msg)

    async def notify_paper_exit(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        exit_price: float,
        gross_pnl: float,
        net_pnl: float,
        pnl_pct: float,
        reason: str,
    ) -> bool:
        is_profit = net_pnl >= 0
        icon = "💰" if is_profit else "🛑"
        status = "TARGET HIT" if "TARGET" in reason.upper() else ("STOP LOSS" if "STOP" in reason.upper() else reason)
        sign = "+" if is_profit else ""
        msg = (
            f"{icon} <b>POSITION CLOSED ({status})</b>\n\n"
            f"📌 <b>Stock:</b> <code>{symbol}</code>\n"
            f"🔢 <b>Shares Sold:</b> {qty}\n"
            f"📥 <b>Entry:</b> ₹{entry_price:.2f} ➔ 📤 <b>Exit:</b> ₹{exit_price:.2f}\n"
            f"💵 <b>Gross PnL:</b> {sign}₹{gross_pnl:,.2f}\n"
            f"🧾 <b>Net PnL (after statutory charges):</b> <b>{sign}₹{net_pnl:,.2f} ({sign}{pnl_pct:.2f}%)</b>\n\n"
            f"<i>Cash recycled back into capital pool</i>"
        )
        return await self.send_message(msg)

    async def notify_portfolio_status(
        self,
        cash: float,
        invested: float,
        open_positions: int,
        total_pnl: float,
    ) -> bool:
        total_capital = cash + invested
        sign = "+" if total_pnl >= 0 else ""
        msg = (
            "📊 <b>PORTFOLIO STATUS UPDATE</b>\n\n"
            f"💵 <b>Available Cash:</b> ₹{cash:,.2f}\n"
            f"📈 <b>Invested in Stocks:</b> ₹{invested:,.2f}\n"
            f"🏦 <b>Total Account Value:</b> ₹{total_capital:,.2f}\n"
            f"📂 <b>Open Swing Positions:</b> {open_positions}\n"
            f"✨ <b>Cumulative Realized PnL:</b> {sign}₹{total_pnl:,.2f}"
        )
        return await self.send_message(msg)
