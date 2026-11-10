"""Telethon client adapter.

Telethon is imported lazily so the API process (and the test suite) do not need
a Telegram session, network access, or credentials. The session file is an
account credential: it is created under TELEGRAM_SESSION_PATH, which is
git-ignored, and is never logged.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.config import settings as default_settings
from app.integrations.telegram.base import ChannelMessage
from app.integrations.telegram.channel import channel_reference, message_url

logger = logging.getLogger(__name__)


class TelegramNotConfigured(RuntimeError):
    pass


class TelethonClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._client = None

    def _validate(self) -> None:
        if not self._settings.telegram_api_id or not self._settings.telegram_api_hash:
            raise TelegramNotConfigured(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set to collect messages"
            )

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        self._validate()
        from telethon import TelegramClient  # lazy: optional at import time

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
            raise TelegramNotConfigured(
                "Telegram session is not authorized; run an interactive login once "
                "and keep the session file outside version control"
            )
        return self._client

    async def fetch_messages(
        self, channel: str, min_id: int, limit: int
    ) -> list[ChannelMessage]:
        client = await self._ensure_client()
        reference = channel_reference(channel)
        entity = await client.get_entity(reference)
        username = getattr(entity, "username", None) or reference
        collected: list[ChannelMessage] = []
        # reverse=True keeps oldest-first so checkpoints move monotonically.
        async for message in client.iter_messages(
            entity, min_id=min_id, limit=limit, reverse=True
        ):
            text = (message.message or "").strip()
            if not text:
                continue
            collected.append(
                ChannelMessage(
                    message_id=int(message.id),
                    text=text,
                    url=message_url(username, int(message.id)),
                    posted_at=message.date,
                )
            )
        logger.info(
            "telegram fetch channel=%s min_id=%s fetched=%s", reference, min_id, len(collected)
        )
        return collected

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self) -> TelethonClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()


class FakeTelegramClient:
    """Deterministic client used by tests and local dry runs."""

    def __init__(
        self,
        messages_by_channel: dict[str, list[ChannelMessage]],
        fail_with: Exception | None = None,
    ):
        self._messages = messages_by_channel
        self._fail_with = fail_with
        self.calls: list[tuple[str, int, int]] = []

    async def fetch_messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]:
        self.calls.append((channel, min_id, limit))
        if self._fail_with is not None:
            raise self._fail_with
        return [
            message
            for message in self._messages.get(channel, [])
            if message.message_id > min_id
        ][:limit]

    async def close(self) -> None:
        return None
