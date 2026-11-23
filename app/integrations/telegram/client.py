"""Telethon collector client.

Collection uses a **user account** session. Telegram bots cannot read channel
history: the API rejects ``GetHistoryRequest`` for bot accounts with
``BotMethodInvalidError``. A bot token is therefore used for publishing only
(see ``publisher.py``).

Telethon is imported lazily so the API process (and the test suite) do not need
a Telegram session, network access, or credentials. The session file is an
account credential: it is created under ``TELEGRAM_SESSION_PATH``, which is
git-ignored, and is never logged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.config import settings as default_settings
from app.integrations.telegram.base import ChannelMessage
from app.integrations.telegram.channel import channel_reference, message_url

logger = logging.getLogger(__name__)


class TelegramNotConfigured(RuntimeError):
    pass


class TelegramBotCannotReadHistory(RuntimeError):
    """Raised when a bot session is used where a user session is required."""


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
            proxy=getattr(self._settings, "telegram_proxy", None),
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            self._client = None
            raise TelegramNotConfigured(
                "Telegram session is not authorized. Collection needs a user "
                "account session: run "
                "'python -m app.integrations.telegram.login --as user' once, then "
                "keep the session file outside version control."
            )
        return self._client

    async def fetch_messages(
        self, channel: str,
        min_id: int,
        limit: int,
        max_age_days: int | None = None,
    ) -> list[ChannelMessage]:
        client = await self._ensure_client()
        reference = channel_reference(channel)
        entity = await client.get_entity(reference)
        username = getattr(entity, "username", None) or reference
        collected: list[ChannelMessage] = []
        # Media-only posts carry no text and are skipped, so request a larger
        # window than the caller asked for. Otherwise a small limit can be
        # consumed entirely by empty posts and yield nothing.
        window = max(limit * 3, limit + 20)

        # The channels carry years of history. Walking it one batch at a time
        # would take hundreds of runs to reach the posts of today, and every
        # message in between is too old to be worth anything. When the gap
        # between the checkpoint and the newest messages is entirely older
        # than the age limit, the collector jumps to the newest messages and
        # continues from there.
        fast_forward = False
        if max_age_days and min_id > 0:
            newest, iterated = await self._newest_messages(entity, limit=window)
            newest_fresh = [message for message in newest if message.message_id > min_id]
            if newest_fresh:
                # The gap is the messages between the checkpoint and the
                # oldest message of the newest window. One sample from the
                # gap decides whether the gap is old, because the message
                # ids of a channel increase with time.
                oldest_in_window = min(message.message_id for message in newest)
                if min_id < oldest_in_window:
                    below = await self._message_below(
                        entity, offset_id=oldest_in_window, offset=iterated
                    )
                    if (
                        below is not None
                        and below.message_id > min_id
                        and below.posted_at is not None
                        and below.posted_at < datetime.now(UTC)
                        - timedelta(days=max_age_days)
                    ):
                        fast_forward = True
                        ordered = sorted(newest_fresh, key=lambda message: message.message_id)
                        skipped = oldest_in_window - min_id
                        logger.info(
                            "telegram fast-forward channel=%s checkpoint=%s newest=%s "
                            "skipped %s old messages",
                            reference,
                            min_id,
                            ordered[-1].message_id if ordered else None,
                            skipped,
                        )
                        return ordered[-limit:]

        # reverse=True keeps oldest-first so checkpoints move monotonically.
        try:
            async for message in client.iter_messages(
                entity, min_id=min_id, limit=window, reverse=True
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
                if len(collected) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001 - normalized for the operator
            if type(exc).__name__ == "BotMethodInvalidError":
                raise TelegramBotCannotReadHistory(
                    "This Telegram session is a bot. Bots cannot read channel "
                    "history (GetHistoryRequest is restricted). Log in with a user "
                    "account: python -m app.integrations.telegram.login --as user"
                ) from exc
            raise
        logger.info(
            "telegram fetch channel=%s min_id=%s fetched=%s fast_forward=%s",
            reference,
            min_id,
            len(collected),
            fast_forward,
        )
        return collected

    async def _newest_messages(self, entity, limit: int) -> tuple[list[ChannelMessage], int]:
        """The newest messages of a channel, newest first.

        Returns the messages and how many messages were read, so the caller
        can ask for the message that sits right below the window.
        """
        messages: list[ChannelMessage] = []
        iterated = 0
        client = await self._ensure_client()
        async for message in client.iter_messages(entity, limit=limit, reverse=False):
            iterated += 1
            text = (message.message or "").strip()
            if not text:
                continue
            messages.append(
                ChannelMessage(
                    message_id=int(message.id),
                    text=text,
                    url=None,
                    posted_at=message.date,
                )
            )
        return messages, iterated

    async def _message_below(
        self, entity, *, offset_id: int = 0, offset: int = 0
    ) -> ChannelMessage | None:
        """The first message below the newest window, used to date the gap."""
        client = await self._ensure_client()
        # Telethon uses offset_id (messages older than offset_id) or add_offset
        kwargs = {"limit": 1, "reverse": False}
        if offset_id > 0:
            kwargs["offset_id"] = offset_id
        elif offset > 0:
            kwargs["add_offset"] = offset

        async for message in client.iter_messages(entity, **kwargs):
            text = (message.message or "").strip()
            if not text:
                continue
            return ChannelMessage(
                message_id=int(message.id),
                text=text,
                url=None,
                posted_at=message.date,
            )
        return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def get_message_dates(
        self, channel: str, ids: list[int]
    ) -> dict[int, datetime]:
        """The publication date of the given messages of a channel.

        Used to fill in the date of posts that were collected before the
        column existed. Missing messages are simply absent from the result.
        """
        client = await self._ensure_client()
        reference = channel_reference(channel)
        entity = await client.get_entity(reference)
        messages = await client.get_messages(entity, ids=list(ids))
        dates: dict[int, datetime] = {}
        for message in messages:
            if message is None or message.date is None:
                continue
            published_at = message.date
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            dates[int(message.id)] = published_at
        return dates

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

    async def fetch_messages(
        self, channel: str,
        min_id: int,
        limit: int,
        max_age_days: int | None = None,
    ) -> list[ChannelMessage]:
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
