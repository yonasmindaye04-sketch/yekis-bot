"""The collector has to reach the posts of today without walking the history.

The live channels carry thousands of old messages. The first collections
started at message 1 and crawled forward fifteen messages per run, so the
posts of today were thousands of messages away. Every message in between was
too old to be worth anything, and the queue only grew.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.telegram.client import TelethonClient


class FakeMessage:
    def __init__(self, message_id: int, text: str, posted_at: datetime | None = None):
        self.id = message_id
        self.message = text
        self.date = posted_at


class FakeEntity:
    username = "some_channel"


class FakeTelethonClient:
    """A stand-in that understands reverse and offset like Telegram does."""

    messages: list[FakeMessage] = []

    def __init__(self, *args, **kwargs):
        self.proxy = kwargs.get("proxy")

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_entity(self, reference):
        return FakeEntity()

    def iter_messages(
        self,
        entity,
        min_id=0,
        limit=100,
        reverse=False,
        offset=0,
        offset_id=0,
        add_offset=0,
    ):
        pool = sorted(FakeTelethonClient.messages, key=lambda message: message.id)

        async def generator():
            if reverse:
                candidates = [message for message in pool if message.id > min_id]
            else:
                candidates = list(reversed(pool))
                if offset_id > 0:
                    candidates = [message for message in candidates if message.id < offset_id]
                offset_val = add_offset or offset
                candidates = candidates[offset_val:]
            for message in candidates[:limit]:
                yield message

        return generator()

    async def disconnect(self):
        return None


@pytest.fixture()
def fake_telethon(monkeypatch):
    FakeTelethonClient.messages = []
    monkeypatch.setitem(
        sys.modules, "telethon", types.SimpleNamespace(TelegramClient=FakeTelethonClient)
    )
    return FakeTelethonClient


def _settings(tmp_path):
    return types.SimpleNamespace(
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_path=str(tmp_path / "session"),
        telegram_bot_session_path=str(tmp_path / "bot"),
        telegram_bot_token=None,
        telegram_proxy=None,
    )


def _history(count: int, newest_id: int) -> list[FakeMessage]:
    """A channel with `count` old messages below `newest_id`."""
    old = datetime.now(UTC) - timedelta(days=200)
    return [
        FakeMessage(newest_id - count + index, f"Old post {index}", old)
        for index in range(count)
    ]


def test_the_collector_jumps_over_an_old_history(fake_telethon, tmp_path):
    now = datetime.now(UTC)
    messages = _history(500, 501)
    messages += [FakeMessage(900, "Looking for a designer. Budget $300.", now)]
    messages += [
        FakeMessage(901, "Another fresh post about a two week project.", now),
        FakeMessage(902, "Third fresh post, remote work.", now),
    ]
    fake_telethon.messages = messages
    client = TelethonClient(settings=_settings(tmp_path))

    fetched = asyncio.run(
        client.fetch_messages("@some_channel", min_id=10, limit=3, max_age_days=30)
    )

    # The three newest posts, not the three oldest ones after the checkpoint.
    assert [message.message_id for message in fetched] == [900, 901, 902]


def test_the_collector_walks_the_history_when_the_gap_is_recent(fake_telethon, tmp_path):
    now = datetime.now(UTC)
    messages = [
        FakeMessage(10, "Post from yesterday", now - timedelta(days=1)),
        FakeMessage(11, "Post from two days ago", now - timedelta(days=2)),
        FakeMessage(12, "Post from today", now),
    ]
    fake_telethon.messages = messages
    client = TelethonClient(settings=_settings(tmp_path))

    fetched = asyncio.run(
        client.fetch_messages("@some_channel", min_id=9, limit=3, max_age_days=30)
    )

    # Nothing was skipped: the gap held a recent post, so the collector
    # keeps its chronological order.
    assert [message.message_id for message in fetched] == [10, 11, 12]


def test_without_an_age_limit_the_history_is_walked_as_before(fake_telethon, tmp_path):
    now = datetime.now(UTC)
    fake_telethon.messages = [
        FakeMessage(10, "Old but collected anyway", now - timedelta(days=200)),
        FakeMessage(900, "A fresh post", now),
    ]
    client = TelethonClient(settings=_settings(tmp_path))

    fetched = asyncio.run(client.fetch_messages("@some_channel", min_id=9, limit=3))

    assert [message.message_id for message in fetched] == [10, 900]
