"""Proxy support for Telegram traffic (Cloudflare tunnel / WARP SOCKS5).

Telegram MTProto traffic can be routed through a SOCKS5 or HTTP proxy, which
is how a Cloudflare-fronted tunnel or a Cloudflare WARP local proxy is used
from a restricted network.
"""

from types import SimpleNamespace

import pytest

from app.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "telegram_proxy_type": "",
        "telegram_proxy_host": "",
        "telegram_proxy_port": 0,
        "telegram_proxy_rdns": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_no_proxy_is_configured_by_default():
    assert _settings().telegram_proxy is None


def test_socks5_proxy_is_exposed_as_a_telethon_tuple():
    settings = _settings(
        telegram_proxy_type="socks5",
        telegram_proxy_host="127.0.0.1",
        telegram_proxy_port=40000,
    )
    assert settings.telegram_proxy == ("socks5", "127.0.0.1", 40000, True)


def test_http_proxy_is_supported():
    settings = _settings(
        telegram_proxy_type="http",
        telegram_proxy_host="proxy.internal",
        telegram_proxy_port=1080,
        telegram_proxy_rdns=False,
    )
    assert settings.telegram_proxy == ("http", "proxy.internal", 1080, False)


def test_incomplete_proxy_configuration_is_ignored():
    assert _settings(telegram_proxy_type="socks5").telegram_proxy is None
    assert (
        _settings(telegram_proxy_host="127.0.0.1", telegram_proxy_port=4000).telegram_proxy
        is None
    )


def test_invalid_proxy_type_is_rejected_clearly():
    settings = _settings(
        telegram_proxy_type="tcp",
        telegram_proxy_host="127.0.0.1",
        telegram_proxy_port=40000,
    )
    with pytest.raises(ValueError, match="TELEGRAM_PROXY_TYPE"):
        _ = settings.telegram_proxy


def test_client_and_publisher_forward_the_proxy(monkeypatch, tmp_path):
    """Both the collector client and the publisher must pass the proxy to Telethon."""
    import sys
    import types

    captured: list[dict] = []

    class FakeTelegramClient:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs)

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return True

        async def disconnect(self):
            return None

        async def start(self, bot_token=None):
            return None

    monkeypatch.setitem(
        sys.modules, "telethon", types.SimpleNamespace(TelegramClient=FakeTelegramClient)
    )

    from app.integrations.telegram.client import TelethonClient
    from app.integrations.telegram.publisher import TelethonPublisher

    proxy = ("socks5", "127.0.0.1", 40000, True)
    settings = SimpleNamespace(
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_path=str(tmp_path / "user"),
        telegram_bot_session_path=str(tmp_path / "bot"),
        telegram_bot_token=None,
        telegram_proxy=proxy,
    )

    import asyncio

    asyncio.run(TelethonClient(settings=settings)._ensure_client())
    asyncio.run(TelethonPublisher(settings=settings)._ensure_client())

    assert len(captured) == 2
    for kwargs in captured:
        assert kwargs["proxy"] == proxy