"""Project-Beta Single Telegram Bot Dispatcher & Interactive C2 Interface.

Handles asynchronous notification delivery for scanner signals, paper trade fills,
exit PnL, portfolio snapshots, and interactive Telegram C2 polling commands
for the single-server runtime.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from typing import Any

import httpx

from core.database import open_beta_database
from core.logging import get_logger

logger = get_logger("telegram.client")

DEFAULT_BOT_TOKEN = "8399759111:AAFTDH54kYhmgkYF90Vt_fssOx9JDPKy6iQ"
DEFAULT_CHAT_ID = "476738400"


class TelegramClient:
    """Async Telegram notification client and interactive C2 interface for Project-Beta."""

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
                bot_token = bot_token or getattr(cfg, "telegram_bot_token", None) or os.getenv("TELEGRAM_BOT_TOKEN") or DEFAULT_BOT_TOKEN
                chat_id = chat_id or getattr(cfg, "telegram_chat_id", None) or os.getenv("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID
            except Exception as e:
                logger.debug("Failed to resolve Telegram credentials from config: %s", e)
                bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or DEFAULT_BOT_TOKEN
                chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID

        self.bot_token = str(bot_token).strip() if bot_token else None
        self.chat_id = str(chat_id).strip() if chat_id else None
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._last_send = 0.0
        self._emergency_stop_triggered = False

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
        target_chat_id: str | None = None,
        parse_mode: str = "HTML",
        disable_preview: bool = True,
        reply_markup: dict | None = None,
    ) -> bool:
        """Send a message to the configured or targeted Telegram chat."""
        cid = target_chat_id or self.chat_id
        if not self.bot_token or not cid:
            logger.debug("Telegram notification skipped: Bot token or Chat ID not configured")
            return False

        # Throttling to respect Telegram API rate limits
        now = time.monotonic()
        elapsed = now - self._last_send
        if elapsed < 0.2:
            await asyncio.sleep(0.2 - elapsed)
        self._last_send = time.monotonic()

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": cid,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

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

    async def edit_message_text(
        self,
        text: str,
        chat_id: str | int,
        message_id: int,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
    ) -> bool:
        """Edit an existing message text and keyboard markup."""
        if not self.bot_token:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        client = await self._get_client()
        try:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200 and bool(resp.json().get("ok"))
        except Exception as e:
            logger.debug("Telegram editMessageText error: %s", e)
            return False

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """Acknowledge button tap to dismiss loading indicator in Telegram."""
        if not self.bot_token or not callback_query_id:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
        client = await self._get_client()
        try:
            resp = await client.post(url, json={"callback_query_id": callback_query_id, "text": text})
            return resp.status_code == 200
        except Exception:
            return False

    # ── Outbound Event Notifications ──────────────────────────────────────────

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
            "<i>Single Server Runtime Online (All 3 services active)</i>\n\n"
            "• Type <b>/help</b> or <b>/status</b> for interactive controls."
        )
        return await self.send_message(msg, reply_markup=self._build_main_keyboard())

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
        target_pct = ((target_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
        sl_pct = ((entry_price - sl_price) / entry_price) * 100 if entry_price > 0 else 0.0
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

    async def notify_learning_calibration(
        self,
        strategy: str,
        weight_multiplier: float,
        threshold: float,
        win_rate: float,
        trades_count: int,
    ) -> bool:
        msg = (
            "🧠 <b>AUTONOMOUS LEARNER CALIBRATION PROMOTED</b>\n\n"
            f"⚡ <b>Strategy:</b> <code>{strategy}</code>\n"
            f"⚖️ <b>Weight Multiplier:</b> <code>{weight_multiplier:.2f}x</code>\n"
            f"🎯 <b>Strict Threshold:</b> <code>{threshold:.1f}</code>\n"
            f"📊 <b>Validated Win Rate:</b> <code>{win_rate:.1f}%</code> ({trades_count} trades evaluated)\n\n"
            "<i>Parameters calibrated safely within [0.80x - 1.20x] boundary.</i>"
        )
        return await self.send_message(msg)

    # ── Interactive Long Polling C2 Loop ──────────────────────────────────────

    def _build_main_keyboard(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 Status", "callback_data": "cb:status"},
                    {"text": "🎯 Signals", "callback_data": "cb:signals"},
                ],
                [
                    {"text": "📈 Positions", "callback_data": "cb:positions"},
                    {"text": "💼 Balance", "callback_data": "cb:balance"},
                ],
                [
                    {"text": "📜 Trades", "callback_data": "cb:trades"},
                    {"text": "⚙️ Learner", "callback_data": "cb:calibrations"},
                ],
                [
                    {"text": "📋 Watchlist", "callback_data": "cb:watchlist"},
                    {"text": "📡 TV Screener", "callback_data": "cb:tv_scan"},
                ],
                [
                    {"text": "🔄 Refresh", "callback_data": "cb:status"},
                    {"text": "🛑 Emergency Stop", "callback_data": "cb:stop"},
                ],
            ]
        }

    def _build_back_keyboard(self, refresh_cb: str = "cb:status") -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "🔙 Main Menu", "callback_data": "cb:menu"},
                    {"text": "🔄 Refresh", "callback_data": refresh_cb},
                ]
            ]
        }

    async def start_polling(self, db_path: str = "data/project_beta.db") -> None:
        """Continuously poll Telegram Bot API for incoming operator commands."""
        if not self.bot_token:
            logger.info("Telegram polling skipped: bot token is not configured")
            return

        logger.info("Project-Beta Telegram C2 polling loop active (Bot: %s...)", self.bot_token[:10])
        offset = None
        client = await self._get_client()
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"

        while True:
            try:
                params: dict[str, Any] = {"timeout": 15}
                if offset is not None:
                    params["offset"] = offset

                resp = await client.get(url, params=params, timeout=20.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            if "message" in update:
                                await self._handle_incoming_message(update["message"], db_path)
                            elif "callback_query" in update:
                                await self._handle_callback_query(update["callback_query"], db_path)
                elif resp.status_code == 409:
                    logger.warning("Telegram conflict 409: Another bot instance is polling. Waiting 10s...")
                    await asyncio.sleep(10.0)
                else:
                    await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Error in Telegram polling loop: %s", exc)
                await asyncio.sleep(2.0)

    async def _handle_incoming_message(self, message: dict, db_path: str) -> None:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        # Always adapt chat_id so replies and alerts reach the active operator
        self.chat_id = str(chat_id)

        cmd = text.split()[0].lower()
        if "@" in cmd:
            cmd = cmd.split("@")[0]

        if cmd in ("/start", "/help", "help", "menu", "/menu"):
            await self._send_menu(chat_id)
        elif cmd in ("/status", "status"):
            await self._send_status(chat_id, db_path)
        elif cmd in ("/positions", "positions"):
            await self._send_positions(chat_id, db_path)
        elif cmd in ("/scan", "/signals", "signals", "scan"):
            await self._send_signals(chat_id, db_path)
        elif cmd in ("/balance", "/capital", "balance", "capital"):
            await self._send_balance(chat_id, db_path)
        elif cmd in ("/trades", "trades"):
            await self._send_trades(chat_id, db_path)
        elif cmd in ("/calibrations", "/learner", "learner", "calibrations"):
            await self._send_calibrations(chat_id, db_path)
        elif cmd in ("/watchlist", "watchlist"):
            await self._send_watchlist(chat_id)
        elif cmd in ("/tv_scan", "/tv", "tv_scan", "tv", "/screener", "screener"):
            await self._send_tv_scan(chat_id)
        elif cmd in ("/stop", "stop", "/emergency_stop"):
            await self._handle_stop_command(chat_id, db_path)
        elif cmd in ("/resume", "resume"):
            await self._handle_resume_command(chat_id, db_path)
        else:
            await self.send_message(
                f"❓ Unknown command <code>{cmd}</code>.\n\nSend <b>/help</b> or use the buttons below:",
                target_chat_id=str(chat_id),
                reply_markup=self._build_main_keyboard(),
            )

    async def _handle_callback_query(self, cb: dict, db_path: str) -> None:
        cb_id = cb.get("id")
        data = cb.get("data", "")
        message = cb.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        msg_id = message.get("message_id")

        if cb_id:
            await self.answer_callback_query(cb_id)

        if not chat_id or not msg_id:
            return

        self.chat_id = str(chat_id)

        if data == "cb:menu":
            await self._render_menu_edit(chat_id, msg_id)
        elif data == "cb:status":
            await self._render_status_edit(chat_id, msg_id, db_path)
        elif data == "cb:positions":
            await self._render_positions_edit(chat_id, msg_id, db_path)
        elif data == "cb:signals":
            await self._render_signals_edit(chat_id, msg_id, db_path)
        elif data == "cb:balance":
            await self._render_balance_edit(chat_id, msg_id, db_path)
        elif data == "cb:trades":
            await self._render_trades_edit(chat_id, msg_id, db_path)
        elif data == "cb:calibrations":
            await self._render_calibrations_edit(chat_id, msg_id, db_path)
        elif data == "cb:watchlist":
            await self._render_watchlist_edit(chat_id, msg_id)
        elif data == "cb:tv_scan":
            await self._render_tv_scan_edit(chat_id, msg_id)
        elif data == "cb:stop":
            await self._handle_stop_callback(chat_id, msg_id, db_path)

    # ── View Renderers ────────────────────────────────────────────────────────

    async def _send_menu(self, chat_id: str | int) -> None:
        text = (
            "🤖 <b>PROJECT-BETA SINGLE SERVER MISSION CONTROL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Indian Cash-Equities Paper Platform (NSE/BSE)</b>\n"
            "All 3 engines (Scanner, Paper Trading, Learner) active in 1 server.\n\n"
            "<b>Available Commands:</b>\n"
            "• <b>/status</b> — Fleet status, uptime & daily limits\n"
            "• <b>/signals</b> — Qualified swing trade signals\n"
            "• <b>/positions</b> — Active open CNC positions\n"
            "• <b>/balance</b> — Cash, capital & portfolio value\n"
            "• <b>/trades</b> — Closed paper trades history\n"
            "• <b>/learner</b> — Strategy calibrations & audit\n"
            "• <b>/watchlist</b> — Configured liquid NSE universe\n"
            "• <b>/tv_scan</b> — Live TradingView NSE equities screener\n"
            "• <b>/stop</b> — Trigger paper emergency stop\n"
            "• <b>/resume</b> — Resume trading entries\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Use inline buttons below for navigation:</i>"
        )
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_main_keyboard())

    async def _render_menu_edit(self, chat_id: str | int, msg_id: int) -> None:
        text = (
            "🤖 <b>PROJECT-BETA SINGLE SERVER MISSION CONTROL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Indian Cash-Equities Paper Platform (NSE/BSE)</b>\n"
            "All 3 engines (Scanner, Paper Trading, Learner) active in 1 server.\n\n"
            "<b>Available Commands:</b>\n"
            "• <b>/status</b> — Fleet status, uptime & daily limits\n"
            "• <b>/signals</b> — Qualified swing trade signals\n"
            "• <b>/positions</b> — Active open CNC positions\n"
            "• <b>/balance</b> — Cash, capital & portfolio value\n"
            "• <b>/trades</b> — Closed paper trades history\n"
            "• <b>/learner</b> — Strategy calibrations & audit\n"
            "• <b>/watchlist</b> — Configured liquid NSE universe\n"
            "• <b>/tv_scan</b> — Live TradingView NSE equities screener\n"
            "• <b>/stop</b> — Trigger paper emergency stop\n"
            "• <b>/resume</b> — Resume trading entries\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_main_keyboard())

    async def _compile_status_text(self, db_path: str) -> str:
        cash = 15000.0
        open_pos = 0
        signals_cnt = 0
        risk_state = "ACTIVE"
        daily_loss = 0.0

        if Path(db_path).exists():
            try:
                conn = await open_beta_database(db_path, read_only=True)
                try:
                    async with conn.execute("SELECT cash FROM beta_paper_account WHERE singleton=1") as cur:
                        row = await cur.fetchone()
                        if row:
                            cash = float(row["cash"])

                    async with conn.execute("SELECT COUNT(*) as cnt FROM beta_positions WHERE status='OPEN'") as cur:
                        row = await cur.fetchone()
                        if row:
                            open_pos = int(row["cnt"])

                    async with conn.execute("SELECT COUNT(*) as cnt FROM beta_signals WHERE status='QUALIFIED'") as cur:
                        row = await cur.fetchone()
                        if row:
                            signals_cnt = int(row["cnt"])

                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    async with conn.execute("SELECT state, realized_pnl, unrealized_pnl, charges FROM beta_paper_daily_risk WHERE trading_date=?", (today,)) as cur:
                        row = await cur.fetchone()
                        if row:
                            risk_state = str(row["state"])
                            daily_loss = abs(min(0.0, float(row["realized_pnl"]) + float(row["unrealized_pnl"]) - float(row["charges"])))
                finally:
                    await conn.close()
            except Exception as e:
                logger.debug("Error compiling status from db: %s", e)

        stop_label = "🔴 EMERGENCY STOPPED" if self._emergency_stop_triggered or risk_state == "EMERGENCY_STOP" else f"🟢 {risk_state}"

        return (
            "📊 <b>PROJECT-BETA FLEET STATUS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Architecture:</b> Single Server (Unified Runtime)\n"
            "• <b>Execution Mode:</b> <code>PAPER</code> (Offline-safe)\n"
            "• <b>Scanner (Stages 1–5):</b> 🟢 ACTIVE\n"
            "• <b>Paper Trading (Stages 6–7):</b> 🟢 ACTIVE\n"
            "• <b>Learner (Stages 8–10):</b> 🟢 ACTIVE\n\n"
            f"💵 <b>Available Cash:</b> ₹{cash:,.2f}\n"
            f"📂 <b>Open Positions:</b> {open_pos} / 3 max\n"
            f"🎯 <b>Signals Qualified:</b> {signals_cnt}\n"
            f"🛡️ <b>Risk Engine:</b> {stop_label}\n"
            f"⚠️ <b>Daily Loss Level:</b> ₹{daily_loss:,.2f} / ₹1,000 max\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>All 3 services running in a single server process.</i>"
        )

    async def _send_status(self, chat_id: str | int, db_path: str) -> None:
        text = await self._compile_status_text(db_path)
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:status"))

    async def _render_status_edit(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        text = await self._compile_status_text(db_path)
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:status"))

    async def _compile_positions_text(self, db_path: str) -> str:
        if not Path(db_path).exists():
            return "📈 <b>ACTIVE OPEN POSITIONS (0 / 3)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n<i>Database not yet initialized.</i>"

        lines = ["📈 <b>ACTIVE OPEN SWING POSITIONS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━"]
        try:
            conn = await open_beta_database(db_path, read_only=True)
            try:
                async with conn.execute("SELECT * FROM beta_positions WHERE status='OPEN'") as cur:
                    positions = [dict(r) for r in await cur.fetchall()]

                if not positions:
                    lines.append("<i>No open positions currently. (3 slots available)</i>")
                else:
                    for pos in positions:
                        sym = f"{pos['exchange']}:{pos['symbol']}"
                        qty = int(pos["quantity"])
                        entry = float(pos["entry_price"])
                        cost = qty * entry

                        # Try to resolve latest quote
                        curr_px = entry
                        async with conn.execute("SELECT price FROM beta_quotes WHERE canonical_symbol=?", (sym,)) as qcur:
                            qrow = await qcur.fetchone()
                            if qrow:
                                curr_px = float(qrow["price"])

                        curr_val = qty * curr_px
                        unrealized = curr_val - cost
                        pct = (unrealized / cost * 100) if cost > 0 else 0.0
                        sign = "+" if unrealized >= 0 else ""
                        lines.append(f"• <b>{sym}</b> — <code>{qty} shares</code>")
                        lines.append(f"  Entry: ₹{entry:.2f} ➔ Current: ₹{curr_px:.2f}")
                        lines.append(f"  Value: ₹{curr_val:,.2f} | PnL: <b>{sign}₹{unrealized:,.2f} ({sign}{pct:.2f}%)</b>")
                        lines.append("")
            finally:
                await conn.close()
        except Exception as e:
            lines.append(f"<i>Error reading positions: {e}</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def _send_positions(self, chat_id: str | int, db_path: str) -> None:
        text = await self._compile_positions_text(db_path)
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:positions"))

    async def _render_positions_edit(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        text = await self._compile_positions_text(db_path)
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:positions"))

    async def _compile_signals_text(self, db_path: str) -> str:
        lines = ["🎯 <b>LATEST QUALIFIED SWING SIGNALS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━"]
        if not Path(db_path).exists():
            lines.append("<i>No signals recorded yet. Scanner is initializing...</i>")
        else:
            try:
                conn = await open_beta_database(db_path, read_only=True)
                try:
                    async with conn.execute(
                        "SELECT * FROM beta_signals WHERE status='QUALIFIED' ORDER BY market_timestamp DESC LIMIT 5"
                    ) as cur:
                        signals = [dict(r) for r in await cur.fetchall()]

                    if not signals:
                        lines.append("<i>No qualified signals in current cycle. Scanning sub-₹250 NSE universe...</i>")
                    else:
                        for s in signals:
                            sym = f"{s['exchange']}:{s['symbol']}"
                            px = float(s["price"])
                            strat = s["strategy"]
                            score = int(s["score"])
                            ts = s["market_timestamp"][:19]
                            lines.append(f"• <b>{sym}</b> — ₹{px:.2f}")
                            lines.append(f"  Strategy: <b>{strat}</b> (Score: <code>{score}/100</code>)")
                            lines.append(f"  Qualified At: <code>{ts}</code>")
                            lines.append("")
                finally:
                    await conn.close()
            except Exception as e:
                lines.append(f"<i>Error reading signals: {e}</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def _send_signals(self, chat_id: str | int, db_path: str) -> None:
        text = await self._compile_signals_text(db_path)
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:signals"))

    async def _render_signals_edit(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        text = await self._compile_signals_text(db_path)
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:signals"))

    async def _compile_balance_text(self, db_path: str) -> str:
        cash = 15000.0
        invested = 0.0

        if Path(db_path).exists():
            try:
                conn = await open_beta_database(db_path, read_only=True)
                try:
                    async with conn.execute("SELECT cash FROM beta_paper_account WHERE singleton=1") as cur:
                        row = await cur.fetchone()
                        if row:
                            cash = float(row["cash"])

                    async with conn.execute("SELECT quantity, entry_price FROM beta_positions WHERE status='OPEN'") as cur:
                        for row in await cur.fetchall():
                            invested += int(row["quantity"]) * float(row["entry_price"])
                finally:
                    await conn.close()
            except Exception as e:
                logger.debug("Error compiling balance: %s", e)

        total = cash + invested
        return (
            "💼 <b>ACCOUNT & CAPITAL BALANCE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Available Paper Cash:</b> ₹{cash:,.2f}\n"
            f"📈 <b>Invested in Equities:</b> ₹{invested:,.2f}\n"
            f"🏦 <b>Total Account Value:</b> ₹{total:,.2f}\n\n"
            "<b>Risk Invariants:</b>\n"
            "• Max Allocation Per Trade: ₹1,000.00\n"
            "• Max Open Positions: 3 (1 per sector)\n"
            "• Stock Price Range: ₹0 – ₹250\n"
            "• Delivery Mode: CNC Whole Shares Only\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    async def _send_balance(self, chat_id: str | int, db_path: str) -> None:
        text = await self._compile_balance_text(db_path)
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:balance"))

    async def _render_balance_edit(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        text = await self._compile_balance_text(db_path)
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:balance"))

    async def _compile_trades_text(self, db_path: str) -> str:
        lines = ["📜 <b>RECENT CLOSED PAPER TRADES</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━"]
        if not Path(db_path).exists():
            lines.append("<i>No trades closed yet. Paper trading in progress.</i>")
        else:
            try:
                conn = await open_beta_database(db_path, read_only=True)
                try:
                    async with conn.execute(
                        "SELECT * FROM beta_journal ORDER BY exit_timestamp DESC LIMIT 6"
                    ) as cur:
                        trades = [dict(r) for r in await cur.fetchall()]

                    if not trades:
                        lines.append("<i>No closed trades recorded yet in journal.</i>")
                    else:
                        for t in trades:
                            sym = f"{t['exchange']}:{t['symbol']}"
                            net = float(t["net_pnl"])
                            sign = "+" if net >= 0 else ""
                            icon = "🟢" if net >= 0 else "🔴"
                            lines.append(f"{icon} <b>{sym}</b> ({t['strategy']})")
                            lines.append(f"   Buy: ₹{float(t['entry_price']):.2f} ➔ Sell: ₹{float(t['exit_price']):.2f}")
                            lines.append(f"   Net PnL: <b>{sign}₹{net:,.2f}</b> ({t['exit_timestamp'][:10]})")
                            lines.append("")
                finally:
                    await conn.close()
            except Exception as e:
                lines.append(f"<i>Error reading trades: {e}</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def _send_trades(self, chat_id: str | int, db_path: str) -> None:
        text = await self._compile_trades_text(db_path)
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:trades"))

    async def _render_trades_edit(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        text = await self._compile_trades_text(db_path)
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:trades"))

    async def _compile_calibrations_text(self, db_path: str) -> str:
        lines = ["⚙️ <b>AUTONOMOUS LEARNER & CALIBRATIONS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━"]
        if not Path(db_path).exists():
            lines.append("<i>Learner initializing. Minimum 3 closed trades required for calibration.</i>")
        else:
            try:
                conn = await open_beta_database(db_path, read_only=True)
                try:
                    async with conn.execute("SELECT COUNT(*) as cnt FROM beta_learning_audit") as cur:
                        row = await cur.fetchone()
                        audits = int(row["cnt"]) if row else 0

                    async with conn.execute("SELECT * FROM beta_learner_calibrations ORDER BY created_at DESC LIMIT 3") as cur:
                        cals = [dict(r) for r in await cur.fetchall()]

                    lines.append(f"• <b>Learner Loop:</b> 🟢 ACTIVE (Stages 8–10)")
                    lines.append(f"• <b>Evaluated Trades in Audit:</b> <code>{audits}</code>")
                    lines.append(f"• <b>Safety Guard:</b> Hard bounded [0.80x – 1.20x]")
                    lines.append("")
                    if cals:
                        lines.append("<b>Recent Promoted Calibrations:</b>")
                        for c in cals:
                            cand = json.loads(c.get("candidate_json") or "{}")
                            strat = cand.get("strategy", "STE")
                            mult = float(cand.get("weight_multiplier", 1.0))
                            th = float(cand.get("confluence_threshold", 85.0))
                            lines.append(f"• <b>{strat}:</b> Multiplier=<code>{mult:.2f}x</code> | Threshold=<code>{th:.1f}</code>")
                    else:
                        lines.append("• <i>All strategies currently at baseline calibration (1.00x weight / 85 threshold)</i>")
                finally:
                    await conn.close()
            except Exception as e:
                lines.append(f"<i>Error reading learner data: {e}</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def _send_calibrations(self, chat_id: str | int, db_path: str) -> None:
        text = await self._compile_calibrations_text(db_path)
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:calibrations"))

    async def _render_calibrations_edit(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        text = await self._compile_calibrations_text(db_path)
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:calibrations"))

    async def _compile_watchlist_text(self) -> str:
        from services.runtime import DEFAULT_UNIVERSE, load_watchlist
        symbols = load_watchlist()
        lines = [
            f"📋 <b>NSE LIQUID WATCHLIST ({len(symbols)} STOCKS)</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "<i>Filtered sub-₹250 liquid Indian equities for swing trading:</i>",
            "",
        ]
        items = [s.trading_symbol for s in symbols]
        chunked = [", ".join(items[i:i + 4]) for i in range(0, len(items), 4)]
        for chunk in chunked:
            lines.append(f"• <code>{chunk}</code>")
        lines.append("")
        lines.append("• Horizon: 1 Week – 1 Month holding")
        lines.append("• 30-day News Sentiment check on every entry")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def _send_watchlist(self, chat_id: str | int) -> None:
        text = await self._compile_watchlist_text()
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:watchlist"))

    async def _render_watchlist_edit(self, chat_id: str | int, msg_id: int) -> None:
        text = await self._compile_watchlist_text()
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:watchlist"))

    async def _compile_tv_scan_text(self) -> str:
        from market.tradingview_client import TradingViewPublicClient
        lines = [
            "📡 <b>TRADINGVIEW LIVE NSE SCREENER (SUB-₹250)</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "<i>Top liquid opportunities sorted by volume directly from TradingView:</i>",
            "",
        ]
        client = TradingViewPublicClient()
        try:
            candidates = await client.scan_market(exchange="NSE", price_floor=1.0, price_ceiling=250.0, limit=8)
            if not candidates:
                lines.append("<i>No matching candidates found from TradingView scanner right now.</i>")
            else:
                for c in candidates:
                    ticker = c.get("ticker", "")
                    sym = ticker.split(":")[-1]
                    px = float(c.get("last_price", 0.0))
                    chg = float(c.get("change") or 0.0)
                    vol = int(c.get("volume") or 0)
                    rsi = f"{float(c['rsi']):.1f}" if c.get("rsi") is not None else "N/A"
                    rec_val = c.get("recommendation")
                    rating = "BUY 🟢" if rec_val and rec_val > 0.1 else ("SELL 🔴" if rec_val and rec_val < -0.1 else "NEUTRAL ⚪")

                    if vol >= 10_000_000:
                        vol_str = f"{vol / 10_000_000:.1f}Cr"
                    elif vol >= 100_000:
                        vol_str = f"{vol / 100_000:.1f}L"
                    else:
                        vol_str = f"{vol:,}"

                    sign = "+" if chg >= 0 else ""
                    lines.append(f"• <b>{sym}</b> — <b>₹{px:.2f}</b> ({sign}{chg:.2f}%)")
                    lines.append(f"  Vol: <code>{vol_str}</code> | RSI: <code>{rsi}</code> | Rec: <code>{rating}</code>")
                    lines.append("")
        except Exception as exc:
            lines.append(f"<i>Error fetching TradingView screener data: {exc}</i>")
        finally:
            await client.close()

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 <i>Public TradingView Scanner API active. No API key required.</i>")
        return "\n".join(lines)

    async def _send_tv_scan(self, chat_id: str | int) -> None:
        text = await self._compile_tv_scan_text()
        await self.send_message(text, target_chat_id=str(chat_id), reply_markup=self._build_back_keyboard("cb:tv_scan"))

    async def _render_tv_scan_edit(self, chat_id: str | int, msg_id: int) -> None:
        text = await self._compile_tv_scan_text()
        await self.edit_message_text(text, chat_id, msg_id, reply_markup=self._build_back_keyboard("cb:tv_scan"))

    async def _handle_stop_command(self, chat_id: str | int, db_path: str) -> None:
        self._emergency_stop_triggered = True
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()
        if Path(db_path).exists():
            try:
                conn = await open_beta_database(db_path)
                try:
                    await conn.execute(
                        """INSERT INTO beta_paper_daily_risk(trading_date, state, updated_at)
                        VALUES(?, 'EMERGENCY_STOP', ?)
                        ON CONFLICT(trading_date) DO UPDATE SET state='EMERGENCY_STOP', updated_at=excluded.updated_at""",
                        (today, now_iso),
                    )
                    await conn.commit()
                finally:
                    await conn.close()
            except Exception as e:
                logger.debug("Error writing emergency stop: %s", e)

        msg = (
            "🛑 <b>EMERGENCY STOP ACTIVATED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• All new paper trade entries are <b>BLOCKED</b>.\n"
            "• Existing open positions continue active SL/TP monitoring.\n"
            "• To resume entries: send <b>/resume</b>."
        )
        await self.send_message(msg, target_chat_id=str(chat_id), reply_markup=self._build_main_keyboard())

    async def _handle_stop_callback(self, chat_id: str | int, msg_id: int, db_path: str) -> None:
        await self._handle_stop_command(chat_id, db_path)

    async def _handle_resume_command(self, chat_id: str | int, db_path: str) -> None:
        self._emergency_stop_triggered = False
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()
        if Path(db_path).exists():
            try:
                conn = await open_beta_database(db_path)
                try:
                    await conn.execute(
                        """INSERT INTO beta_paper_daily_risk(trading_date, state, updated_at)
                        VALUES(?, 'ACTIVE', ?)
                        ON CONFLICT(trading_date) DO UPDATE SET state='ACTIVE', updated_at=excluded.updated_at""",
                        (today, now_iso),
                    )
                    await conn.commit()
                finally:
                    await conn.close()
            except Exception as e:
                logger.debug("Error resuming risk state: %s", e)

        msg = (
            "▶️ <b>TRADING ENTRIES RESUMED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Emergency stop cleared.\n"
            "• Paper execution engine resumed normal signal evaluation."
        )
        await self.send_message(msg, target_chat_id=str(chat_id), reply_markup=self._build_main_keyboard())
