"""
PROJECT-ALPHA — Learner Telegram Notifier & Interactive Interface (Stages 8–10).

Dedicated Telegram C2 Bot for Alpha-Learner:
  - Handles interactive commands: /start, /help, /status, /learn, /insights, /calibrations, /audit, /rollback
  - Long polling loop with zero open incoming ports required
  - Real-time push alerts for Canary promotions, mistake discoveries, and emergency rollbacks
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from core.bus.event_bus import EventBus
from core.bus.event_types import EventType
from core.config import AppConfig
from core.logging import get_logger
from telegram.telegram import TelegramClient

logger = get_logger("learner.telegram.notifier")


class LearnerTelegramNotifier:
    """Dedicated Telegram interface for Autonomous AI Learner."""

    def __init__(
        self,
        bus: EventBus,
        config: AppConfig | Any,
        learner_service: Any,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self._learner_service = learner_service

        token = (
            bot_token
            or getattr(config, "learner_bot_token", None)
            or getattr(config, "telegram_bot_token", None)
        )
        chat = (
            chat_id
            or getattr(config, "learner_chat_id", None)
            or getattr(config, "telegram_chat_id", None)
        )

        self.client = TelegramClient(
            bot_token=token,
            chat_id=chat,
            resolve_default_credentials=False,
        )
        self._running = False
        self._polling_task: asyncio.Task | None = None
        self._last_update_id = 0

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured

    async def start(self) -> None:
        """Start push alert subscriptions and polling loop."""
        self._bus.subscribe(EventType.CALIBRATION_UPDATED, self._on_calibration_updated)
        if self.client.is_configured:
            self._running = True
            self._polling_task = asyncio.create_task(self._poll_loop())
            logger.info("Learner Telegram Interactive C2 Bot started.")
            # Dispatch startup announcement if chat_id is known
            if self.client.default_chat_id:
                try:
                    await self.client.send_message(
                        "🧠 <b>PROJECT-ALPHA — AI LEARNER ONLINE</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "Stages 8–10 Autonomous Learning & Canary Calibrator is active.\n"
                        "Type <code>/help</code> or <code>/status</code> for controls."
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Could not send startup greeting: %s", exc)
        else:
            logger.info("Learner Telegram bot token not configured; running in passive mode.")

    async def stop(self) -> None:
        """Stop polling loop and unsubscribe events."""
        self._running = False
        self._bus.unsubscribe(EventType.CALIBRATION_UPDATED, self._on_calibration_updated)
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        logger.info("Learner Telegram Notifier stopped.")

    async def _on_calibration_updated(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        """Push notification on parameter promotions or emergency rollbacks."""
        if not self.client.is_configured:
            return

        status = str(payload.get("status", "")).upper()
        bot_name = payload.get("bot_name", "FLEET")
        action = payload.get("action_taken", "CALIBRATION")
        new_mult = payload.get("new_multiplier", 1.0)
        new_thresh = payload.get("new_threshold", 85.0)

        if status == "ROLLED_BACK":
            text = (
                f"🚨 <b>EMERGENCY SAFETY ROLLBACK — {bot_name}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Trigger:</b> 2 Consecutive Post-Promotion Losses\n"
                f"<b>Action:</b> Reset to baseline configuration\n"
                f"<b>Weight Multiplier:</b> <code>{new_mult:.2f}x</code>\n"
                f"<b>Min Confluence Score:</b> <code>{new_thresh:.1f}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Capital protection invariant enforced.</i>"
            )
        elif status == "PROMOTED":
            text = (
                f"🟢 <b>CANARY CALIBRATION PROMOTED — {bot_name}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Action:</b> {action}\n"
                f"<b>Pre-Validation Backtest:</b> PASSED (PF >= 1.0)\n"
                f"<b>New Weight Multiplier:</b> <code>{new_mult:.2f}x</code>\n"
                f"<b>New Confluence Threshold:</b> <code>{new_thresh:.1f}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Now active in Canary state with 2-loss auto-rollback guard.</i>"
            )
        else:
            return

        try:
            await self.client.send_message(text)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to dispatch learner telegram notification: %s", exc)

    async def _poll_loop(self) -> None:
        """Background long polling loop for inbound operator commands."""
        token = self.client.bot_token
        url = f"https://api.telegram.org/bot{token}/getUpdates"

        async with httpx.AsyncClient(timeout=25.0) as http_client:
            while self._running:
                try:
                    params = {"offset": self._last_update_id + 1, "timeout": 15}
                    resp = await http_client.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        for update in updates:
                            self._last_update_id = update["update_id"]
                            message = update.get("message", {})
                            text = (message.get("text") or "").strip()
                            chat_id = message.get("chat", {}).get("id")

                            if chat_id and text:
                                # Save chat_id if not previously stored
                                if not self.client.default_chat_id:
                                    self.client._chat_id = str(chat_id)
                                await self._handle_command(text, str(chat_id))
                    elif resp.status_code in (401, 404):
                        logger.error("Telegram token invalid (HTTP %d). Stopping polling.", resp.status_code)
                        break
                    else:
                        await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Telegram polling transient error: %s", exc)
                    await asyncio.sleep(3)

    async def _handle_command(self, text: str, chat_id: str) -> None:
        """Parse and execute interactive C2 commands."""
        parts = text.strip().split()
        if not parts:
            return

        raw_cmd = parts[0].lower().split("@")[0]
        cmd = raw_cmd if raw_cmd.startswith("/") else f"/{raw_cmd}"
        args = parts[1:]
        logger.info("Learner Telegram executing '%s' for chat_id %s", cmd, chat_id)

        if cmd in ("/start", "/help"):
            reply = (
                "🧠 <b>PROJECT-ALPHA — AI LEARNER & CANARY BOT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Autonomous Stages 8–10 Control Center\n\n"
                "<b>Commands:</b>\n"
                "• <code>/status</code> — Current learner loop health & active parameters\n"
                "• <code>/learn</code> — Trigger an immediate learning & calibration cycle\n"
                "• <code>/insights</code> — View recent mistake patterns detected\n"
                "• <code>/calibrations</code> — View strategy multipliers & score thresholds\n"
                "• <code>/audit</code> — View Canary promotions & rollback audit trail\n"
                "• <code>/rollback [BOT]</code> — Reset bot to baseline parameters (1.0x / 85)\n"
                "• <code>/help</code> — Show this menu\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Continuous Autonomous Machine Learning & Capital Protection</i>"
            )

        elif cmd == "/status":
            last_report = self._learner_service.last_report
            active = await self._learner_service.get_active_calibrations()
            last_time = last_report.get("timestamp", "Never (Cold Start)")
            trade_count = last_report.get("trade_count", 0)

            lines = [
                "🧠 <b>LEARNER FLEET STATUS</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"<b>Loop State:</b> {'🟢 ACTIVE' if self._learner_service.is_running else '🟡 IDLE'}",
                f"<b>Last Cycle:</b> <code>{last_time[:19] if last_time != 'Never' else last_time}</code>",
                f"<b>Recent Trades Evaluated:</b> <code>{trade_count}</code>",
                f"<b>Last Promoted:</b> <code>{last_report.get('promoted_count', 0)}</code>",
                f"<b>Last Rollbacks:</b> <code>{last_report.get('rolled_back_count', 0)}</code>",
                "",
                "<b>Active Calibrations Cache:</b>",
            ]
            if isinstance(active, list) and active:
                for c in active:
                    bot = c.get("bot_name", "UNKNOWN")
                    mult = float(c.get("weight_multiplier", 1.0))
                    thresh = float(c.get("strict_threshold", 85.0))
                    lines.append(f"• <b>{bot}:</b> <code>{mult:.2f}x</code> | Min Score: <code>{thresh:.1f}</code>")
            else:
                lines.append("• <i>No custom overrides active (all on baseline 1.00x / 85.0)</i>")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
            reply = "\n".join(lines)

        elif cmd in ("/learn", "/cycle"):
            reply = "⏳ <i>Executing autonomous learning & validation cycle pass...</i>"
            await self.client.send_message(reply, target_chat_id=chat_id)

            report = await self._learner_service.run_cycle()
            reply = (
                "⚡ <b>LEARNING CYCLE COMPLETED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Status:</b> <code>{report.get('status', 'OK')}</code>\n"
                f"<b>Trades Ingested:</b> <code>{report.get('trade_count', 0)}</code>\n"
                f"<b>Mistake Insights:</b> <code>{report.get('insights_count', 0)}</code>\n"
                f"<b>Calibrations Evaluated:</b> <code>{report.get('calibrations_count', 0)}</code>\n"
                f"<b>Canary Promoted:</b> <code>{report.get('promoted_count', 0)}</code>\n"
                f"<b>Degradations Rejected:</b> <code>{report.get('rejected_count', 0)}</code>\n"
                f"<b>Safety Rollbacks:</b> <code>{report.get('rolled_back_count', 0)}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━"
            )

        elif cmd == "/insights":
            insights = await self._learner_service._learning_repo.get_active_insights()
            if not insights:
                reply = "ℹ️ <i>No active mistake patterns detected yet. Fleet trades are executing cleanly.</i>"
            else:
                lines = [
                    "🔍 <b>RECENT MISTAKE PATTERNS & INSIGHTS</b>",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━",
                ]
                for ins in insights[:6]:
                    bot = ins.get("bot_name", "FLEET")
                    pattern = ins.get("pattern_type", "UNKNOWN")
                    severity = ins.get("severity", "LOW")
                    summary = ins.get("lesson_summary", "")
                    rec = ins.get("recommended_adjustment", "")
                    lines.append(f"• <b>[{severity}] {bot} — {pattern}</b>")
                    lines.append(f"  <i>Summary:</i> {summary}")
                    if rec:
                        lines.append(f"  <i>Recommendation:</i> <code>{rec}</code>")
                    lines.append("")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
                reply = "\n".join(lines)

        elif cmd == "/calibrations":
            active = await self._learner_service.get_active_calibrations()
            lines = [
                "⚙️ <b>ACTIVE STRATEGY CALIBRATIONS</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]
            if isinstance(active, list) and active:
                for c in active:
                    bot = c.get("bot_name", "UNKNOWN")
                    mult = float(c.get("weight_multiplier", 1.0))
                    thresh = float(c.get("strict_threshold", 85.0))
                    updated = str(c.get("updated_at", ""))[:19]
                    lines.append(f"• <b>{bot}</b>: Multiplier=<code>{mult:.2f}x</code>, Threshold=<code>{thresh:.1f}</code>")
                    lines.append(f"  <i>Updated:</i> <code>{updated}</code>")
            else:
                lines.append("<i>All strategies currently at standard baseline (1.00x / 85.0).</i>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
            reply = "\n".join(lines)

        elif cmd == "/audit":
            history = await self._learner_service.get_audit_history(limit=8)
            if not history:
                reply = "ℹ️ <i>Audit trail is empty. No parameter promotions or rollbacks recorded yet.</i>"
            else:
                lines = [
                    "📜 <b>CANARY AUDIT TRAIL (LAST 8)</b>",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━",
                ]
                for ev in history:
                    bot = ev.get("bot_name", "BOT")
                    action = ev.get("action_taken", "AUDIT")
                    status = ev.get("status", "OK")
                    mult = float(ev.get("new_multiplier", 1.0))
                    thresh = float(ev.get("new_threshold", 85.0))
                    ts = str(ev.get("created_at", ""))[:19]
                    icon = "🟢" if status == "PROMOTED" else ("🚨" if status == "ROLLED_BACK" else "⚪")
                    lines.append(f"{icon} <b>{bot} — {action} [{status}]</b>")
                    lines.append(f"   Multiplier: <code>{mult:.2f}x</code> | Threshold: <code>{thresh:.1f}</code> ({ts})")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
                reply = "\n".join(lines)

        elif cmd == "/rollback":
            target_bot = args[0].upper() if args else "ALL"
            if target_bot in ("STE", "HDA", "VCP", "BBS"):
                await self._learner_service._feedback_repo.upsert_active_calibration(
                    bot_name=target_bot,
                    weight_multiplier=1.0,
                    strict_threshold=85.0,
                )
                await self._learner_service._feedback_repo.update_canary_losses(
                    target_bot, 0, active=False
                )
                reply = (
                    f"✅ <b>MANUAL ROLLBACK COMPLETE</b>\n"
                    f"Strategy <code>{target_bot}</code> has been reset to baseline (1.00x weight, 85.0 min score)."
                )
            else:
                for b in ("STE", "HDA", "VCP", "BBS"):
                    await self._learner_service._feedback_repo.upsert_active_calibration(
                        bot_name=b,
                        weight_multiplier=1.0,
                        strict_threshold=85.0,
                    )
                    await self._learner_service._feedback_repo.update_canary_losses(
                        b, 0, active=False
                    )
                reply = "✅ <b>FLEET ROLLBACK COMPLETE</b>\nAll bot strategies have been reset to baseline (1.00x weight, 85.0 min score)."

        else:
            reply = f"Unknown command: <code>{cmd}</code>. Send <code>/help</code> to see available commands."

        await self.client.send_message(reply, target_chat_id=chat_id)
