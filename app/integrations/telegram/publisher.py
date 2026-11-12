"""Telegram publisher adapters.

The real adapter is lazy so importing the API never opens a Telegram session.
Publishing is always guarded before this layer is reached; a publisher is a
dumb transport that never decides whether content may be sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.config import settings as default_settings
from app.integrations.telegram.channel import channel_reference

logger = logging.getLogger(__name__)


class PublisherError(Exception):
    pass


@dataclass(frozen=True)
class MessageRef:
    external_message_id: str


class PublisherPort(Protocol):
    async def send_message(self, channel: str, text: str) -> MessageRef: ...

    async def close(self) -> None: ...


class TelethonPublisher:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._client = None

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not (
            self._settings.telegram_api_id
            and self._settings.telegram_api_hash
        ):
            raise PublisherError("telegram credentials are not configured")
        from telethon import TelegramClient

        # A bot token is preferred for publishing: bots may send messages.
        # Bots may NOT read channel history, so collection uses a user session.
        use_bot = bool(self._settings.telegram_bot_token)
        configured_path = (
            self._settings.telegram_bot_session_path
            if use_bot
            else self._settings.telegram_session_path
        )
        if not configured_path:
            raise PublisherError("telegram session path is not configured")
        session_path = Path(configured_path)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(
            str(session_path),
            self._settings.telegram_api_id,
            self._settings.telegram_api_hash,
            proxy=getattr(self._settings, "telegram_proxy", None),
        )
        if use_bot:
            await self._client.start(bot_token=self._settings.telegram_bot_token)
            return self._client

        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            self._client = None
            raise PublisherError("telegram session is not authorized")
        return self._client

    async def send_message(self, channel: str, text: str) -> MessageRef:
        reference = channel_reference(channel)
        # 1. Prefer standard Telegram Bot HTTP API when bot token is configured
        # This is serverless-friendly (Vercel, AWS Lambda) and avoids MTProto session disk locks.
        if self._settings.telegram_bot_token:
            import httpx

            target_chat = f"@{reference}" if not reference.startswith(("-", "@")) else reference
            url = f"https://api.telegram.org/bot{self._settings.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": target_chat,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            try:
                async with httpx.AsyncClient(timeout=15.0) as http_client:
                    response = await http_client.post(url, json=payload)
                    data = response.json()
                    # Retry without Markdown parse mode if formatting fails
                    if not data.get("ok") and "can't parse" in data.get("description", "").lower():
                        payload.pop("parse_mode", None)
                        response = await http_client.post(url, json=payload)
                        data = response.json()
                    if not data.get("ok"):
                        err_desc = data.get("description", "Unknown Telegram API error")
                        raise PublisherError(f"telegram api error: {err_desc}")
                    msg_id = str(data["result"]["message_id"])
                    return MessageRef(external_message_id=msg_id)
            except PublisherError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise PublisherError(f"telegram send failed: {type(exc).__name__}: {exc}") from exc

        # 2. Fallback to Telethon MTProto client for user session publishing
        try:
            client = await self._ensure_client()
            entity = await client.get_entity(reference)
            message = await client.send_message(entity, text, link_preview=False)
            return MessageRef(external_message_id=str(message.id))
        except PublisherError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized transport error
            # Never include the message body or credentials in the error.
            raise PublisherError(f"telegram send failed: {type(exc).__name__}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


@dataclass
class FakePublisher:
    sent: list[tuple[str, str]] = field(default_factory=list)
    fail_with: Exception | None = None
    fail_times: int = 0

    async def send_message(self, channel: str, text: str) -> MessageRef:
        if self.fail_with is not None and self.fail_times != 0:
            if self.fail_times > 0:
                self.fail_times -= 1
            raise self.fail_with
        self.sent.append((channel, text))
        return MessageRef(external_message_id=f"fake-{len(self.sent)}")

    async def close(self) -> None:
        return None
