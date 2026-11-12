import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.integrations.telegram.publisher import FakePublisher
from app.models import Publication
from app.security.auth import Principal
from app.services.formatter import MAX_MESSAGE_LENGTH, format_publication
from app.services.ingestion import ingest_post
from app.services.publishing import (
    ChannelNotAllowed,
    NotApproved,
    PublicationFailed,
    PublicationNotDue,
    PublicationUnknown,
    PublishingDisabled,
    publish_due,
    publish_opportunity,
    publish_publication,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
PRINCIPAL = Principal(name="test-reviewer", role="reviewer")


def _settings(**overrides):
    values = {
        "publishing_enabled": True,
        "publishing_allowed_channels": ["@audit_publish"],
        "publishing_max_attempts": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _approved(db, source, external_id="publish-1", **overrides):
    opportunity, _ = ingest_post(
        db,
        source_id=source.id,
        external_id=external_id,
        original_text=(
            "Looking for a React developer to build a dashboard. "
            "Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks."
        ),
    )
    opportunity.review_status = "approved"
    for field, value in overrides.items():
        setattr(opportunity, field, value)
    db.commit()
    db.refresh(opportunity)
    return opportunity


def test_unapproved_opportunity_cannot_be_published(db_session, source):
    from app.services.ingestion import ingest_post as ingest

    opportunity, _ = ingest(
        db_session,
        source_id=source.id,
        external_id="pending-1",
        original_text="Need a python developer. Pay $1,000.",
    )
    fake = FakePublisher()

    with pytest.raises(NotApproved):
        asyncio.run(
            publish_opportunity(
                db_session,
                opportunity_id=opportunity.id,
                channel="@audit_publish",
                principal=PRINCIPAL,
                publisher=fake,
                settings=_settings(),
                now=NOW,
            )
        )
    assert fake.sent == []
    assert db_session.scalar(select(func.count()).select_from(Publication)) == 0


def test_publishing_disabled_sends_nothing(db_session, source):
    opportunity = _approved(db_session, source, "disabled-1")
    fake = FakePublisher()

    with pytest.raises(PublishingDisabled):
        asyncio.run(
            publish_opportunity(
                db_session,
                opportunity_id=opportunity.id,
                channel="@audit_publish",
                principal=PRINCIPAL,
                publisher=fake,
                settings=_settings(publishing_enabled=False),
                now=NOW,
            )
        )
    assert fake.sent == []


def test_channel_must_be_allowlisted(db_session, source):
    opportunity = _approved(db_session, source, "channel-1")
    with pytest.raises(ChannelNotAllowed):
        asyncio.run(
            publish_opportunity(
                db_session,
                opportunity_id=opportunity.id,
                channel="@some_other_channel",
                principal=PRINCIPAL,
                publisher=FakePublisher(),
                settings=_settings(),
                now=NOW,
            )
        )


def test_publish_sends_once_with_attribution(db_session, source):
    opportunity = _approved(db_session, source, "success-1")
    fake = FakePublisher()

    publication = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opportunity.id,
            channel="@audit_publish",
            principal=PRINCIPAL,
            publisher=fake,
            settings=_settings(),
            now=NOW,
        )
    )

    assert publication.status == "published"
    assert publication.external_message_id == "fake-1"
    assert publication.created_by == "test-reviewer"
    assert publication.published_at is not None
    assert len(fake.sent) == 1
    channel, text = fake.sent[0]
    assert channel == "audit_publish"
    assert "Compensation: 300 USD" in text
    assert "APPLY ON YEKIS" in text
    assert "https://yekis.com" in text
    assert "Source:" not in text


def test_replaying_the_same_publication_does_not_double_send(db_session, source):
    opportunity = _approved(db_session, source, "replay-1")
    fake = FakePublisher()
    settings = _settings()

    first = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opportunity.id,
            channel="@audit_publish",
            principal=PRINCIPAL,
            publisher=fake,
            settings=settings,
            now=NOW,
        )
    )
    second = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opportunity.id,
            channel="@audit_publish",
            principal=PRINCIPAL,
            publisher=fake,
            settings=settings,
            now=NOW,
        )
    )

    assert first.id == second.id
    assert len(fake.sent) == 1
    assert db_session.scalar(select(func.count()).select_from(Publication)) == 1


def test_a_sending_row_is_never_resent_automatically(db_session, source):
    opportunity = _approved(db_session, source, "sending-1")
    publication = Publication(
        opportunity_id=opportunity.id,
        channel="audit_publish",
        status="sending",
        attempts=1,
        created_by="test-reviewer",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(publication)
    db_session.commit()

    with pytest.raises(PublicationUnknown):
        asyncio.run(
            publish_opportunity(
                db_session,
                opportunity_id=opportunity.id,
                channel="@audit_publish",
                principal=PRINCIPAL,
                publisher=FakePublisher(),
                settings=_settings(),
                now=NOW,
            )
        )


def test_failed_send_is_recorded_and_can_be_retried(db_session, source):
    opportunity = _approved(db_session, source, "failure-1")
    fake = FakePublisher(fail_with=RuntimeError("telegram down"), fail_times=1)
    settings = _settings()

    with pytest.raises(PublicationFailed):
        asyncio.run(
            publish_opportunity(
                db_session,
                opportunity_id=opportunity.id,
                channel="@audit_publish",
                principal=PRINCIPAL,
                publisher=fake,
                settings=settings,
                now=NOW,
            )
        )
    failed = db_session.scalar(select(Publication))
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert "telegram down" in failed.error

    published = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opportunity.id,
            channel="@audit_publish",
            principal=PRINCIPAL,
            publisher=fake,
            settings=settings,
            now=NOW,
        )
    )
    assert published.status == "published"
    assert published.attempts == 2
    assert len(fake.sent) == 1


def test_scheduled_publication_waits_for_due_time(db_session, source):
    opportunity = _approved(db_session, source, "scheduled-1")
    fake = FakePublisher()
    settings = _settings()

    publication = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opportunity.id,
            channel="@audit_publish",
            principal=PRINCIPAL,
            publisher=fake,
            settings=settings,
            scheduled_at=NOW + timedelta(hours=1),
            now=NOW,
        )
    )
    assert publication.status == "scheduled"
    assert fake.sent == []

    with pytest.raises(PublicationNotDue):
        asyncio.run(
            publish_publication(
                db_session,
                publication,
                publisher=fake,
                settings=settings,
                now=NOW,
            )
        )

    due = asyncio.run(
        publish_due(
            db_session,
            publisher=fake,
            settings=settings,
            now=NOW + timedelta(hours=2),
        )
    )
    assert len(due) == 1
    assert due[0].status == "published"
    assert len(fake.sent) == 1


def test_approval_is_rechecked_at_send_time(db_session, source):
    opportunity = _approved(db_session, source, "recheck-1")
    fake = FakePublisher()
    settings = _settings()
    publication = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opportunity.id,
            channel="@audit_publish",
            principal=PRINCIPAL,
            publisher=fake,
            settings=settings,
            scheduled_at=NOW + timedelta(hours=1),
            now=NOW,
        )
    )

    opportunity.review_status = "rejected"
    db_session.commit()

    due = asyncio.run(
        publish_due(
            db_session,
            publisher=fake,
            settings=settings,
            now=NOW + timedelta(hours=2),
        )
    )
    assert due == []
    assert fake.sent == []
    db_session.refresh(publication)
    assert publication.status == "scheduled"


def test_formatting_within_length_bounds(db_session, source):
    opportunity = _approved(db_session, source, "long-1")
    opportunity.description = "x" * 5000
    db_session.commit()
    db_session.refresh(opportunity)

    text = format_publication(opportunity, source)
    assert len(text) <= MAX_MESSAGE_LENGTH
    assert "APPLY ON YEKIS" in text
    assert "https://yekis.com" in text
    assert "Source:" not in text


def test_format_publication_includes_client_name(db_session, source):
    opportunity = _approved(db_session, source, "client-test-1", client_name="ILRI Ethiopia")
    text = format_publication(opportunity, source)
    assert "• Client: ILRI Ethiopia" in text
    assert "• Type: Freelance Job" in text
    assert "APPLY ON YEKIS" in text
    assert "https://yekis.com" in text
