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
            and self._settings.telegram_session_path
        ):
            raise PublisherError("telegram credentials are not configured")
        from telethon import TelegramClient

        session_path = Path(self._settings.telegram_session_path)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(
            str(session_path),
            self._settings.telegram_api_id,
            self._settings.telegram_api_hash,
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            self._client = None
            raise PublisherError("telegram session is not authorized")
        return self._client

    async def send_message(self, channel: str, text: str) -> MessageRef:
        try:
            reference = channel_reference(channel)
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
