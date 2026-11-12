"""Telethon client behaviour that matters for real channels.

The live collection run against a public channel exposed a real inefficiency:
media-only posts carry no text and are skipped, but they still consumed the
requested fetch limit, so a small limit could return nothing at all.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.integrations.telegram.client import TelethonClient


class FakeMessage:
    def __init__(self, message_id: int, text: str):
        self.id = message_id
        self.message = text
        self.date = None


class FakeEntity:
    username = "some_channel"


class FakeTelethonClient:
    """Stands in for telethon.TelegramClient."""

    messages: list[FakeMessage] = []
    last_limit: int | None = None

    def __init__(self, *args, **kwargs):
        self.proxy = kwargs.get("proxy")

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_entity(self, reference):
        return FakeEntity()

    def iter_messages(self, entity, min_id=0, limit=100, reverse=False):
        FakeTelethonClient.last_limit = limit

        async def generator():
            for message in FakeTelethonClient.messages:
                if message.id > min_id:
                    yield message

        return generator()

    async def disconnect(self):
        return None


@pytest.fixture()
def fake_telethon(monkeypatch):
    FakeTelethonClient.messages = []
    FakeTelethonClient.last_limit = None
    monkeypatch.setitem(
        sys.modules, "telethon", types.SimpleNamespace(TelegramClient=FakeTelethonClient)
    )
    return FakeTelethonClient


def _settings(tmp_path, **overrides):
    values = {
        "telegram_api_id": 1,
        "telegram_api_hash": "hash",
        "telegram_session_path": str(tmp_path / "session"),
        "telegram_bot_session_path": str(tmp_path / "bot"),
        "telegram_bot_token": None,
        "telegram_proxy": None,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_empty_messages_do_not_consume_the_fetch_limit(fake_telethon, tmp_path):
    """A limit of 1 must still return a message when earlier posts are media-only."""
    fake_telethon.messages = [
        FakeMessage(1, ""),
        FakeMessage(2, "   "),
        FakeMessage(3, "Looking for a Python developer. Budget $500."),
        FakeMessage(4, "Another real post about design work."),
    ]
    client = TelethonClient(settings=_settings(tmp_path))

    messages = asyncio.run(client.fetch_messages("@some_channel", min_id=0, limit=1))

    assert len(messages) == 1
    assert messages[0].message_id == 3
    assert messages[0].text.startswith("Looking for a Python developer")
    assert fake_telethon.last_limit and fake_telethon.last_limit > 1, (
        "the fetch window must be larger than the requested limit"
    )


def test_limit_is_respected_for_text_messages(fake_telethon, tmp_path):
    fake_telethon.messages = [
        FakeMessage(10, "First post"),
        FakeMessage(11, "Second post"),
        FakeMessage(12, "Third post"),
    ]
    client = TelethonClient(settings=_settings(tmp_path))

    messages = asyncio.run(client.fetch_messages("@some_channel", min_id=0, limit=2))

    assert [m.message_id for m in messages] == [10, 11]


def test_min_id_filters_already_collected_messages(fake_telethon, tmp_path):
    fake_telethon.messages = [
        FakeMessage(10, "First post"),
        FakeMessage(11, "Second post"),
        FakeMessage(12, "Third post"),
    ]
    client = TelethonClient(settings=_settings(tmp_path))

    messages = asyncio.run(client.fetch_messages("@some_channel", min_id=11, limit=5))

    assert [m.message_id for m in messages] == [12]


def test_message_urls_are_built_from_the_channel_username(fake_telethon, tmp_path):
    fake_telethon.messages = [FakeMessage(42, "A post")]
    client = TelethonClient(settings=_settings(tmp_path))

    messages = asyncio.run(client.fetch_messages("@some_channel", min_id=0, limit=1))

    assert messages[0].url == "https://t.me/some_channel/42"