import asyncio
import logging
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.integrations.telegram.base import ChannelMessage
from app.integrations.telegram.channel import InvalidChannelReference, channel_reference
from app.integrations.telegram.client import (
    FakeTelegramClient,
    TelegramBotCannotReadHistory,
    TelegramNotConfigured,
    TelethonClient,
)
from app.integrations.telegram.collector import backoff_seconds, collect_source, collect_sources
from app.models import CollectedPost, Opportunity, Source
from app.services.ingestion import ingest_post

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _message(message_id: int, text: str) -> ChannelMessage:
    return ChannelMessage(
        message_id=message_id,
        text=text,
        url=f"https://t.me/audit_test_channel/{message_id}",
        posted_at=NOW,
    )


def test_collects_new_messages_once_across_two_runs(db_session, source):
    fake = FakeTelegramClient(
        {
            source.source_url: [
                _message(1, "Looking for a Python developer. Budget $500."),
                _message(2, "Need a graphic designer for a logo. Budget ETB 15,000."),
            ]
        }
    )

    first = asyncio.run(collect_source(db_session, source, fake, now=NOW))
    assert first.status == "collected"
    assert first.fetched == 2
    assert first.ingested == 2
    assert first.duplicates == 0
    assert first.last_message_id == 2

    db_session.refresh(source)
    assert source.last_collected_message_id == 2
    assert source.consecutive_failures == 0
    assert source.last_success_at is not None

    second = asyncio.run(collect_source(db_session, source, fake, now=NOW + timedelta(minutes=15)))
    assert second.status == "collected"
    assert second.fetched == 0
    assert second.ingested == 0
    assert fake.calls == [
        (source.source_url, 0, 100),
        (source.source_url, 2, 100),
    ]

    assert db_session.scalar(select(func.count()).select_from(CollectedPost)) == 2
    assert db_session.scalar(select(func.count()).select_from(Opportunity)) == 2


def test_existing_external_id_is_counted_as_duplicate(db_session, source):
    ingest_post(
        db_session,
        source_id=source.id,
        external_id="5",
        original_text="Looking for a React developer to build a dashboard. Budget $300.",
    )
    fake = FakeTelegramClient(
        {
            source.source_url: [
                _message(5, "Looking for a React developer to build a dashboard. Budget $300."),
                _message(6, "We need a copywriter for a landing page. Pay $120."),
            ]
        }
    )

    result = asyncio.run(collect_source(db_session, source, fake, now=NOW))

    assert result.fetched == 2
    assert result.ingested == 1
    assert result.duplicates == 1
    assert db_session.scalar(select(func.count()).select_from(CollectedPost)) == 2
    db_session.refresh(source)
    assert source.last_collected_message_id == 6


def test_failure_records_backoff_without_advancing_checkpoint(db_session, source):
    fake = FakeTelegramClient(
        {source.source_url: []}, fail_with=RuntimeError("channel unavailable")
    )

    failed = asyncio.run(collect_source(db_session, source, fake, now=NOW))

    assert failed.status == "failed"
    assert "channel unavailable" in failed.error
    db_session.refresh(source)
    assert source.consecutive_failures == 1
    assert source.last_collected_message_id is None
    assert source.next_attempt_at is not None
    assert source.last_error.startswith("RuntimeError:")

    # Still inside the backoff window: no fetch is attempted.
    calls_before = len(fake.calls)
    skipped = asyncio.run(collect_source(db_session, source, fake, now=NOW))
    assert skipped.status == "skipped"
    assert skipped.skipped_reason == "backoff"
    assert len(fake.calls) == calls_before

    # After the window, a healthy fetch succeeds and clears the failure state.
    db_session.refresh(source)
    source.next_attempt_at = NOW - timedelta(seconds=1)
    db_session.commit()
    recovered = asyncio.run(
        collect_source(
            db_session,
            source,
            FakeTelegramClient(
                {source.source_url: [_message(10, "Need a data analyst. Budget $400.")]}
            ),
            now=NOW,
        )
    )
    assert recovered.status == "collected"
    assert recovered.ingested == 1
    db_session.refresh(source)
    assert source.consecutive_failures == 0
    assert source.last_error is None
    assert source.last_collected_message_id == 10


def test_inactive_and_non_telegram_sources_are_skipped(db_session, source):
    source.is_active = False
    db_session.commit()
    result = asyncio.run(collect_source(db_session, source, FakeTelegramClient({}), now=NOW))
    assert result.status == "skipped"
    assert result.skipped_reason == "source_inactive"

    source.is_active = True
    source.source_type = "website"
    db_session.commit()
    result = asyncio.run(collect_source(db_session, source, FakeTelegramClient({}), now=NOW))
    assert result.status == "skipped"
    assert result.skipped_reason == "not_a_telegram_source"


def test_collect_sources_only_reads_active_telegram_sources(db_session, source):
    other = Source(
        name="Website source",
        source_type="website",
        source_url="https://example.com/feed",
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    fake = FakeTelegramClient({source.source_url: [_message(1, "Need a Python developer. $200.")]})
    results = asyncio.run(collect_sources(db_session, fake, now=NOW))

    assert [result.source_id for result in results] == [source.id]
    assert results[0].ingested == 1


def test_message_text_is_never_logged(db_session, source, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "SUPER_SECRET_MESSAGE_BODY_123"
    fake = FakeTelegramClient({source.source_url: [_message(1, marker)]})

    asyncio.run(collect_source(db_session, source, fake, now=NOW))

    assert marker not in caplog.text
    assert source.source_url not in caplog.text


def test_channel_reference_rejects_invite_links_and_accepts_public_links():
    assert channel_reference("https://t.me/yekis_jobs") == "yekis_jobs"
    assert channel_reference("@yekis_jobs") == "yekis_jobs"
    assert channel_reference("https://t.me/yekis_jobs/123") == "yekis_jobs"
    with pytest.raises(InvalidChannelReference):
        channel_reference("https://t.me/+abc123")
    with pytest.raises(InvalidChannelReference):
        channel_reference("https://example.com/channel")
    with pytest.raises(InvalidChannelReference):
        channel_reference("")


def test_telethon_client_refuses_to_start_without_credentials():
    stub_settings = type(
        "S", (), {"telegram_api_id": None, "telegram_api_hash": None}
    )()
    client = TelethonClient(settings=stub_settings)
    with pytest.raises(TelegramNotConfigured):
        asyncio.run(client.fetch_messages("@channel", 0, 10))


def test_telethon_collector_never_uses_a_bot_token(monkeypatch, tmp_path):
    """Collection must use a user session: bots cannot read channel history."""

    class FakeTelegramClient:
        def __init__(self, *args, **kwargs):
            self.started_with_bot_token = False

        async def start(self, bot_token=None):
            self.started_with_bot_token = bot_token is not None
            return None

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return True

        async def disconnect(self):
            return None

    monkeypatch.setitem(
        sys.modules, "telethon", types.SimpleNamespace(TelegramClient=FakeTelegramClient)
    )
    stub_settings = types.SimpleNamespace(
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_session_path=str(tmp_path / "session"),
        telegram_bot_token="test-bot-token",
    )
    client = TelethonClient(settings=stub_settings)

    asyncio.run(client._ensure_client())

    assert client._client.started_with_bot_token is False


def test_bot_session_history_error_is_explained(monkeypatch, tmp_path):
    """A bot session must produce actionable guidance, not a raw RPC error."""

    class BotMethodInvalidError(Exception):
        pass

    class FakeTelegramClient:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return True

        async def get_entity(self, reference):
            return type("Entity", (), {"username": "channel"})()

        def iter_messages(self, *args, **kwargs):
            async def generator():
                raise BotMethodInvalidError(
                    "The method you tried to invoke cannot be executed as a bot"
                )
                yield  # pragma: no cover

            return generator()

        async def disconnect(self):
            return None

    monkeypatch.setitem(
        sys.modules, "telethon", types.SimpleNamespace(TelegramClient=FakeTelegramClient)
    )
    stub_settings = types.SimpleNamespace(
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_session_path=str(tmp_path / "session"),
        telegram_bot_token="test-bot-token",
    )
    client = TelethonClient(settings=stub_settings)

    with pytest.raises(TelegramBotCannotReadHistory, match="--as user"):
        asyncio.run(client.fetch_messages("@channel", 0, 10))


def test_backoff_is_bounded_and_increases():
    assert 30 <= backoff_seconds(1) <= 40
    assert backoff_seconds(10) <= 3600 + 360
    assert backoff_seconds(50) <= 3600 + 360
