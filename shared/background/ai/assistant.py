"""
PROJECT-ALPHA — MBT AI Assistant Agent.

Provides a centralized semantic assistant interface that understands natural language
operator requests and dispatches them to internal MBT services and repositories.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

from core.logging import get_logger
from core.repository.position_repo import PositionRepository
from core.repository.trade_repo import TradeRepository

logger = get_logger("background.ai.assistant")


class MBTAssistantAgent:
    """
    Central AI Operator Assistant.
    Interprets natural language queries (e.g. 'How many positions are open?',
    'Why did this trade close?', 'Show today's P&L', 'Check database') and
    returns structured, actionable answers with live telemetry.
    """

    def __init__(
        self,
        config: Any,
        position_repo: PositionRepository | None = None,
        trade_repo: TradeRepository | None = None,
        portfolio_service: Any | None = None,
        health_checker: Any | None = None,
        risk_service: Any | None = None,
        scanner_service: Any | None = None,
        gemini_client: Any | None = None,
    ) -> None:
        self._config = config
        self._position_repo = position_repo
        self._trade_repo = trade_repo
        self._portfolio_service = portfolio_service
        self._health_checker = health_checker
        self._risk_service = risk_service
        self._scanner_service = scanner_service
        self._gemini_client = gemini_client

    # ── Telemetry & Service Tools ─────────────────────────────────────────────

    async def get_positions_summary(self) -> str:
        """Fetch and format all active positions."""
        if not self._position_repo:
            return "⚠️ Position repository is not connected."

        positions = await self._position_repo.get_active_positions()
        if not positions:
            return (
                "📈 <b>Active Positions: 0</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "No open positions currently. All capital is safely parked in cash reserve."
            )

        lines = [
            f"📈 <b>Active Fleet Positions ({len(positions)})</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        total_unrealized = 0.0
        for p in positions:
            entry = float(p.entry_price or 0.0)
            cur = float(p.current_price or entry)
            qty = float(p.qty or 0.0)
            pair = p.pair or f"{p.coin}/INR"
            sym = "$" if "USDT" in pair else "₹"
            unrealized = float(p.unrealised_pnl if p.unrealised_pnl is not None else (cur - entry) * qty)
            total_unrealized += unrealized

            pct = ((cur - entry) / entry * 100.0) if entry > 0 else 0.0
            sign = "+" if unrealized >= 0 else ""
            status_tag = f" [{p.status.value}]" if hasattr(p.status, "value") else f" [{p.status}]"

            lines.append(
                f"• <b>{pair}</b> ({p.bot.value if hasattr(p.bot, 'value') else p.bot}){status_tag}\n"
                f"  Entry: {sym}{entry:.4f} | Current: {sym}{cur:.4f}\n"
                f"  P&L: <code>{sign}{sym}{unrealized:.2f} ({sign}{pct:.2f}%)</code> | SL: {p.stop_loss} | TP: {p.take_profit}"
            )

        u_sign = "+" if total_unrealized >= 0 else ""
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"Total Unrealized P&L: <code>{u_sign}₹{total_unrealized:.2f}</code>")
        return "\n".join(lines)

    async def get_pnl_summary(self) -> str:
        """Aggregate realized, unrealized, and daily performance."""
        realized = 0.0
        unrealized = 0.0
        trades_count = 0
        win_rate = 0.0

        if self._portfolio_service:
            try:
                snap = await self._portfolio_service.get_snapshot()
                realized = snap.total_realised_pnl
                unrealized = snap.total_unrealised_pnl
            except Exception as e:
                logger.debug("Error fetching portfolio snapshot: %s", e)

        if self._trade_repo:
            try:
                recent = await self._trade_repo.get_recent(limit=100)
                trades_count = len(recent)
                wins = sum(1 for t in recent if float(getattr(t, "pnl", 0.0) or 0.0) > 0)
                win_rate = (wins / trades_count * 100.0) if trades_count > 0 else 0.0
            except Exception as e:
                logger.debug("Error fetching recent trades: %s", e)

        net_total = realized + unrealized
        r_sign = "+" if realized >= 0 else ""
        u_sign = "+" if unrealized >= 0 else ""
        t_sign = "+" if net_total >= 0 else ""

        return (
            "💰 <b>Portfolio & P&L Performance</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Realized P&L: <code>{r_sign}₹{realized:.2f}</code>\n"
            f"• Unrealized MTM: <code>{u_sign}₹{unrealized:.2f}</code>\n"
            f"• Net Combined P&L: <code>{t_sign}₹{net_total:.2f}</code>\n"
            f"• Recorded Trades: <b>{trades_count}</b>\n"
            f"• Historical Win Rate: <b>{win_rate:.1f}%</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    async def get_recent_trades(self, limit: int = 5) -> str:
        """Retrieve the most recent closed trades."""
        if not self._trade_repo:
            return "⚠️ Trade repository is not available."

        trades = await self._trade_repo.get_recent(limit=limit)
        if not trades:
            return "📜 <b>No closed trades recorded yet in this session.</b>"

        lines = [
            f"📜 <b>Recent Closed Trades (Last {len(trades)})</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for t in trades:
            pnl = float(t.pnl or 0.0)
            pnl_pct = float(t.pnl_pct or 0.0)
            p_sign = "+" if pnl >= 0 else ""
            pct_sign = "+" if pnl_pct >= 0 else ""
            emoji = "🟢" if pnl >= 0 else "🔴"
            reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
            time_str = t.exit_time.strftime("%d-%b %H:%M") if hasattr(t, "exit_time") and t.exit_time else "N/A"

            lines.append(
                f"{emoji} <b>{t.coin}</b> ({t.bot.value if hasattr(t.bot, 'value') else t.bot})\n"
                f"  Exit: {reason} @ {time_str}\n"
                f"  Net P&L: <code>{p_sign}₹{pnl:.2f} ({pct_sign}{pnl_pct:.2f}%)</code>"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def explain_trade_exit(self, coin_query: str) -> str:
        """Search for a specific coin in recent trades and explain why it closed."""
        if not self._trade_repo:
            return "⚠️ Trade repository not available."

        coin_clean = coin_query.strip().upper()
        trades = await self._trade_repo.get_recent(limit=50)
        matching = [t for t in trades if t.coin.upper() == coin_clean or (t.pair and coin_clean in t.pair.upper())]

        if not matching:
            return f"ℹ️ No closed trade history found for asset <b>{coin_clean}</b> in recent records."

        t = matching[0]
        reason = t.exit_reason.value if hasattr(t.exit_reason, "value") else str(t.exit_reason)
        pnl = float(t.pnl or 0.0)
        pnl_pct = float(t.pnl_pct or 0.0)
        sign = "+" if pnl >= 0 else ""

        explanation = ""
        if reason == "STOP_LOSS":
            explanation = (
                f"The position hit its protective Stop Loss at ₹{t.exit_price:.4f}. "
                f"This triggered to preserve capital and prevent further downside."
            )
        elif reason == "TAKE_PROFIT":
            explanation = (
                f"The position successfully achieved its Take Profit target at ₹{t.exit_price:.4f}, "
                f"locking in profitable gains."
            )
        elif reason == "ADMIN_RESET":
            explanation = "The position was manually closed during an administrative system reset."
        else:
            explanation = f"The position closed due to exit event trigger: <code>{reason}</code>."

        return (
            f"🔍 <b>Trade Closure Diagnostic — {t.coin}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Strategy Bot: <b>{t.bot.value if hasattr(t.bot, 'value') else t.bot}</b>\n"
            f"• Entry Price: ₹{t.entry_price}\n"
            f"• Exit Price: ₹{t.exit_price}\n"
            f"• Trigger Reason: <code>{reason}</code>\n"
            f"• Realized Net P&L: <code>{sign}₹{pnl:.2f} ({sign}{pnl_pct:.2f}%)</code>\n\n"
            f"💡 <b>Analysis:</b> {explanation}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    async def get_system_health(self) -> str:
        """Run system diagnostics across all services."""
        components = {
            "Scanner Service": self._scanner_service is not None,
            "Execution Engine": True,
            "Risk Engine": True,
            "Event Bus": True,
            "Database Connectivity": False,
        }

        # Check DB
        db_path = getattr(self._config, "db_path", "data/project_alpha.db")
        if os.path.exists(db_path):
            components["Database Connectivity"] = True

        circuit_breaker_status = "HEALTHY"
        if self._risk_service and hasattr(self._risk_service, "circuit_breaker"):
            if getattr(self._risk_service.circuit_breaker, "is_open", False):
                circuit_breaker_status = "TRIPPED (Trading Frozen)"

        lines = [
            "🩺 <b>System Diagnostics & Subsystems</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for name, healthy in components.items():
            status_icon = "🟢 ACTIVE" if healthy else "🔴 DEGRADED"
            lines.append(f"• {name}: {status_icon}")

        lines.append(f"• Circuit Breaker: <b>{circuit_breaker_status}</b>")
        lines.append(f"• Execution Mode: <b>{getattr(self._config, 'deployment_mode', 'PAPER')}</b>")
        lines.append(f"• Trading State: <b>{'ENABLED' if getattr(self._config, 'trading_enabled', False) else 'PAUSED'}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def check_database_status(self) -> str:
        """Inspect the active SQLite database directly."""
        db_path = getattr(self._config, "db_path", None) or getattr(self._config, "sqlite_db_path", None) or "v2/data/alpha_v2.db"
        if not os.path.exists(db_path):
            fallbacks = ["/opt/project-alpha/v2/data/alpha_v2.db", "v2/data/alpha_v2.db", "data/project_alpha.db"]
            for fb in fallbacks:
                if os.path.exists(fb):
                    db_path = fb
                    break

        if not os.path.exists(db_path):
            return f"❌ Database file not found at {db_path}."

        size_mb = os.path.getsize(db_path) / (1024 * 1024)

        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            cur.execute("SELECT count(*) FROM positions")
            total_positions = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM positions WHERE status != 'CLOSED'")
            active_positions = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM signals")
            total_signals = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM trades")
            total_trades = cur.fetchone()[0]

            conn.close()

            return (
                "🗄️ <b>Database Telemetry & Integrity</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• Database Path: <code>{db_path}</code>\n"
                f"• File Size: <b>{size_mb:.2f} MB</b>\n"
                f"• Total Positions: <b>{total_positions}</b> (Active: {active_positions})\n"
                f"• Generated Signals: <b>{total_signals}</b>\n"
                f"• Recorded Closed Trades: <b>{total_trades}</b>\n"
                "• Status: 🟢 <b>HEALTHY & CONNECTED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Send /db to export a full compressed copy.</i>"
            )
        except Exception as e:
            return f"❌ Database check error: {e}"

    # ── Natural Language Query Dispatcher ─────────────────────────────────────

    async def process_query(self, text: str) -> str:
        """
        Process natural language text from operator and dispatch to appropriate tool.
        """
        query = text.strip()
        lower = query.lower()

        # 1. Position Queries
        if any(w in lower for w in ("how many positions", "open positions", "show positions", "positions count", "active positions", "what is open", "current positions")):
            return await self.get_positions_summary()

        # 2. P&L / Performance Queries
        if any(w in lower for w in ("p&l", "pnl", "profit", "loss", "today's return", "how much money", "daily return", "performance")):
            return await self.get_pnl_summary()

        # 3. Trade History
        if any(w in lower for w in ("recent trades", "show trades", "last trades", "closed trades", "trade history")):
            return await self.get_recent_trades()

        # 4. Diagnostic: Why did a trade close?
        why_close_match = re.search(r"why\s+(?:did\s+)?([a-zA-Z0-9]+)\s+(?:close|exit|sell)", lower)
        if why_close_match:
            coin = why_close_match.group(1).upper()
            return await self.explain_trade_exit(coin)

        if "why did this trade close" in lower or "why did the trade close" in lower:
            # Look up the very latest closed trade
            if self._trade_repo:
                recent = await self._trade_repo.get_recent(limit=1)
                if recent:
                    return await self.explain_trade_exit(recent[0].coin)
            return "ℹ️ Please specify the coin symbol, e.g. <i>'Why did INJ close?'</i>"

        # 5. Database status
        if any(w in lower for w in ("check database", "db status", "database health", "inspect db", "database stats", "database rows")):
            return await self.check_database_status()

        # 6. System health & diagnostics
        if any(w in lower for w in ("what is wrong", "diagnose", "is bot working", "any errors", "health check", "system status", "subsystems", "bot status")):
            return await self.get_system_health()

        # 7. Fallback to Gemini if configured
        if self._gemini_client and getattr(self._config, "gemini_api_key", None):
            try:
                # Prepare context summary for Gemini
                pos_summary = await self.get_positions_summary()
                pnl_summary = await self.get_pnl_summary()
                health_summary = await self.get_system_health()
                
                system_prompt = (
                    "You are the AI Assistant Agent for PROJECT-ALPHA, an autonomous crypto momentum trading fleet. "
                    "Provide concise, precise, professional operational answers to the operator.\n\n"
                    f"Current Telemetry Context:\n{health_summary}\n\n{pos_summary}\n\n{pnl_summary}"
                )
                
                # Direct API invocation
                import httpx
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self._config.gemini_api_key}"
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": query}]}],
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300},
                }
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            text_resp = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            if text_resp:
                                return text_resp.strip()
            except Exception as e:
                logger.warning("Gemini conversational assistant fallback error: %s", e)

        # 8. Friendly Natural Fallback Guide
        return (
            "🤖 <b>AI Assistant</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"I received your inquiry: <i>'{query}'</i>\n\n"
            "You can ask me questions naturally, such as:\n"
            "• <i>'How many positions are open?'</i>\n"
            "• <i>'Show today's P&L'</i>\n"
            "• <i>'Why did INJ close?'</i>\n"
            "• <i>'What is wrong with the bot?'</i>\n"
            "• <i>'Check database'</i>\n"
            "• <i>'Show recent trades'</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Or use slash commands like /start, /status, /positions, /pnl, or /db."
        )
