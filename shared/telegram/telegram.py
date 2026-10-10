"""
PROJECT-ALPHA Async Telegram Dispatcher.

Dispatches formatted alerts to a configured Telegram chat with rate-limiting,
exponential backoff, and graceful fallback when credentials are unconfigured.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from core.logging import get_logger

logger = get_logger("telegram.telegram")


class TelegramClient:
    """Asynchronous Telegram Bot API messaging and polling client."""

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
                bot_token = bot_token or getattr(cfg, "alert_bot_token", None) or getattr(cfg, "telegram_bot_token", None)
                chat_id = chat_id or getattr(cfg, "alert_chat_id", None) or getattr(cfg, "telegram_chat_id", None)
            except Exception:
                pass
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._last_send_time = 0.0
        self._min_interval = (
            0.05  # Min interval between dispatches to avoid Telegram flood limits
        )

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token)

    @property
    def bot_token(self) -> str | None:
        return self._bot_token

    @property
    def default_chat_id(self) -> str | None:
        return self._chat_id

    async def send_message(
        self,
        text: str,
        target_chat_id: str | None = None,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        max_retries: int = 2,
    ) -> bool:
        """Send an HTML/Markdown formatted message to a target or default Telegram chat."""
        cid = chat_id or target_chat_id or self._chat_id
        if not self._bot_token or not cid:
            logger.info(
                "Telegram not configured; message logged locally",
                extra={"preview": text[:100]},
            )
            return False

        # Rate limiter pacing
        elapsed = time.monotonic() - self._last_send_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload: dict = {
            "chat_id": cid,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload)
                    self._last_send_time = time.monotonic()
                    if resp.status_code == 200:
                        return True
                    logger.warning(
                        "Telegram API error response",
                        extra={"status": resp.status_code, "body": resp.text[:200]},
                    )
            except Exception as exc:
                if attempt == max_retries:
                    logger.error(
                        "Failed to dispatch Telegram message after retries",
                        extra={"error": str(exc)},
                    )
                    return False
                await asyncio.sleep(0.5 * (attempt + 1))

        return False

    async def send_document(
        self,
        document_path: str,
        caption: str = "",
        target_chat_id: str | None = None,
        parse_mode: str = "HTML",
        max_retries: int = 2,
    ) -> bool:
        """Send a document (file) to a target or default Telegram chat."""
        cid = target_chat_id or self._chat_id
        if not self._bot_token or not cid:
            return False

        # Rate limiter pacing
        elapsed = time.monotonic() - self._last_send_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        url = f"https://api.telegram.org/bot{self._bot_token}/sendDocument"
        
        for attempt in range(max_retries + 1):
            try:
                import os
                if not os.path.exists(document_path):
                    logger.error(f"File not found: {document_path}")
                    return False
                    
                async with httpx.AsyncClient(timeout=120.0) as client:
                    with open(document_path, "rb") as f:
                        files = {"document": (os.path.basename(document_path), f)}
                        data = {"chat_id": cid, "caption": caption, "parse_mode": parse_mode}
                        resp = await client.post(url, data=data, files=files)
                        
                    self._last_send_time = time.monotonic()
                    if resp.status_code == 200:
                        return True
                    logger.warning(
                        "Telegram sendDocument error response",
                        extra={"status": resp.status_code, "body": resp.text[:200]},
                    )
            except Exception as exc:
                if attempt == max_retries:
                    logger.error(
                        "Failed to dispatch Telegram document after retries",
                        extra={"error": str(exc)},
                    )
                    return False
                await asyncio.sleep(0.5 * (attempt + 1))

        return False

    async def edit_message_text(
        self,
        text: str,
        chat_id: str | int,
        message_id: int,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        max_retries: int = 2,
    ) -> bool:
        """Edit an existing Telegram message in-place (used for dynamic inline keyboard navigation)."""
        if not self._bot_token:
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/editMessageText"
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        return True
                    # 400 Bad Request if message text is unchanged — ignore harmless edit errors
                    if (
                        resp.status_code == 400
                        and "message is not modified" in resp.text.lower()
                    ):
                        return True
                    logger.warning(
                        "Telegram editMessageText error",
                        extra={"status": resp.status_code, "body": resp.text[:200]},
                    )
            except Exception as exc:
                if attempt == max_retries:
                    logger.error(
                        "Failed to edit Telegram message after retries",
                        extra={"error": str(exc)},
                    )
                    return False
                await asyncio.sleep(0.3 * (attempt + 1))

        return False

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge an incoming inline keyboard button tap."""
        if not self._bot_token:
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/answerCallbackQuery"
        payload: dict = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as exc:
            logger.debug("Failed to answer callback query: %s", exc)
            return False

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 20,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch incoming user messages and button callbacks using long-polling."""
        if not self._bot_token:
            return []

        url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
        params: dict = {"timeout": timeout, "limit": limit}
        if offset is not None:
            params["offset"] = offset

        try:
            # Client timeout should be greater than long-poll timeout
            async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        return data.get("result", [])
                elif resp.status_code == 409:
                    # Another instance is polling (e.g. VPS cloud server daemon)
                    await asyncio.sleep(5.0)
                    return []
                logger.warning(
                    "Telegram getUpdates returned status %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            # Normal long-polling timeout when no updates occurred
            return []
        except Exception as exc:
            logger.error("Error fetching Telegram updates: %s", exc)

        return []

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        """Register bot commands with Telegram API for native slash autofill/autocomplete."""
        if not self._bot_token:
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/setMyCommands"
        payload = {"commands": commands}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200 and resp.json().get("ok"):
                    logger.info("Telegram setMyCommands succeeded for %d commands", len(commands))
                    return True
                logger.warning(
                    "Telegram setMyCommands error: status=%s body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:
            logger.error("Failed to register Telegram bot commands: %s", exc)
        return False

