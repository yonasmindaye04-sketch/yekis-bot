"""Publisher account model: bots may publish, users may publish, collection needs users."""

import asyncio
import sys
import types

import pytest

from app.integrations.telegram.publisher import PublisherError, TelethonPublisher


class FakeTelegramClient:
    def __init__(self, *args, **kwargs):
        self.started_with_bot_token = None
        self.connected = False

    async def start(self, bot_token=None):
        self.started_with_bot_token = bot_token
        return None

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        return None


def _install_fake(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "telethon", types.SimpleNamespace(TelegramClient=FakeTelegramClient)
    )


def _settings(tmp_path, **overrides):
    values = {
        "telegram_api_id": 123,
        "telegram_api_hash": "hash",
        "telegram_session_path": str(tmp_path / "user"),
        "telegram_bot_session_path": str(tmp_path / "bot"),
        "telegram_bot_token": None,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_publisher_prefers_the_bot_token_when_configured(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    publisher = TelethonPublisher(
        settings=_settings(tmp_path, telegram_bot_token="bot-token-value")
    )

    asyncio.run(publisher._ensure_client())

    assert publisher._client.started_with_bot_token == "bot-token-value"


def test_publisher_falls_back_to_the_user_session(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    publisher = TelethonPublisher(settings=_settings(tmp_path))

    asyncio.run(publisher._ensure_client())

    assert publisher._client.started_with_bot_token is None
    assert publisher._client.connected is True


def test_publisher_requires_credentials(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    publisher = TelethonPublisher(
        settings=_settings(tmp_path, telegram_api_id=None, telegram_api_hash=None)
    )

    with pytest.raises(PublisherError, match="not configured"):
        asyncio.run(publisher._ensure_client())
