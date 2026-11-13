"""Autonomous publishing tests.

Covers the publishing safeguards: eligibility, limits, idempotency, duplicate
prevention, cancellation after rejection, and the at-most-once rule for
uncertain send states.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.integrations.telegram.publisher import FakePublisher
from app.models import AutonomyDecision, Publication
from app.services.autonomy import (
    evaluate_opportunity,
    resolve_config,
    run_autonomous_publish_cycle,
    update_config,
)
from app.services.publishing import PublishingError
from tests.test_autonomy import JOB_POST, StubProvider

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)

SECOND_JOB_POST = (
    "We need a graphic designer for a brand identity. Fixed price $450. "
    "Delivery in 5 days. Please send your portfolio."
)


def _settings(**overrides):
    values = {
        "publishing_enabled": True,
        "publishing_allowed_channels": ["@target"],
        "publishing_max_attempts": 3,
        "platform_url": "https://yekis.com",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _configure(db, **overrides) -> None:
    values = {
        "enabled": True,
        "mode": "production",
        "auto_approval_threshold": 0.95,
        "approved_source_ids": [],
        "approved_categories": [],
        "daily_publish_limit": 5,
        "min_interval_seconds": 0,
        "pilot_channel": "@pilot",
        "target_channel": "@target",
    }
    values.update(overrides)
    update_config(db, values, actor="test-admin")


def _approved_opportunity(db, source, external_id: str, text: str = JOB_POST):
    from app.services.ingestion import ingest_post

    opportunity, _ = ingest_post(
        db, source_id=source.id, external_id=external_id, original_text=text
    )
    evaluate_opportunity(db, opportunity, provider=StubProvider())
    db.refresh(opportunity)
    assert opportunity.review_status == "approved"
    return opportunity


def _cycle(db, publisher, settings=None, now=NOW, limit=10):
    return asyncio.run(
        run_autonomous_publish_cycle(
            db,
            publisher=publisher,
            settings=settings or _settings(),
            now=now,
            limit=limit,
        )
    )


# --- Happy path ------------------------------------------------------------


def test_eligible_opportunity_is_published_automatically(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-1")

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert len(publisher.sent) == 1
    assert publisher.sent[0][0] == "target"
    assert "APPLY ON YEKIS" in publisher.sent[0][1]
    assert len(outcome["published"]) == 1

    publication = db_session.scalar(select(Publication))
    assert publication.status == "published"
    assert publication.is_autonomous is True
    assert publication.autonomy_mode == "production"
    assert publication.created_by == "system:autonomy"
    assert publication.content_hash


def test_published_message_carries_the_channel_style(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-style")

    publisher = FakePublisher()
    _cycle(db_session, publisher)

    text = publisher.sent[0][1]
    assert "APPLY ON YEKIS" in text
    assert "https://yekis.com" in text
    assert len(text) <= 4096


# --- Guards ----------------------------------------------------------------


def test_non_job_content_is_never_published(db_session, source):
    from app.services.ingestion import ingest_post

    _configure(db_session, approved_source_ids=[source.id])
    ingest_post(
        db_session,
        source_id=source.id,
        external_id="pub-education",
        original_text=(
            "In between job opportunities, we will send you some tips to improve "
            "your chances to land a job. Today part 4 of skills you need to have."
        ),
    )

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert publisher.sent == []
    assert outcome["published"] == []


def test_high_risk_opportunity_is_never_published(db_session, source):
    from app.services.ingestion import ingest_post

    _configure(db_session, approved_source_ids=[source.id])
    ingest_post(
        db_session,
        source_id=source.id,
        external_id="pub-scam",
        original_text=(
            "URGENT! Send 500 birr registration fee via telebirr to get a $5000 "
            "remote job. Pay upfront."
        ),
    )

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert publisher.sent == []
    assert outcome["published"] == []


def test_rejected_opportunity_is_not_published(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    opportunity = _approved_opportunity(db_session, source, "pub-rejected")
    opportunity.review_status = "rejected"
    db_session.commit()

    publisher = FakePublisher()
    _cycle(db_session, publisher)

    assert publisher.sent == []


def test_source_outside_the_allowlist_is_not_published(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-source")
    _configure(db_session, approved_source_ids=[source.id + 500])

    publisher = FakePublisher()
    _cycle(db_session, publisher)

    assert publisher.sent == []


def test_shadow_mode_publishes_nothing(db_session, source):
    _configure(db_session, mode="production", approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-shadow")
    _configure(db_session, mode="shadow", approved_source_ids=[source.id])

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert publisher.sent == []
    assert outcome["skipped"]


def test_disabled_autonomy_publishes_nothing(db_session, source):
    _configure(db_session, mode="production", approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-disabled")
    _configure(db_session, enabled=False, approved_source_ids=[source.id])

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert publisher.sent == []
    assert outcome["skipped"]


def test_channel_outside_the_publishing_allowlist_is_not_used(db_session, source):
    _configure(
        db_session, approved_source_ids=[source.id], target_channel="@elsewhere"
    )

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert publisher.sent == []
    assert outcome["skipped"]


# --- Limits and duplicates -------------------------------------------------


def test_daily_publishing_limit_is_enforced(db_session, source):
    _configure(db_session, approved_source_ids=[source.id], daily_publish_limit=1)
    _approved_opportunity(db_session, source, "pub-limit-1")
    _approved_opportunity(db_session, source, "pub-limit-2", SECOND_JOB_POST)

    publisher = FakePublisher()
    _cycle(db_session, publisher)
    assert len(publisher.sent) == 1

    second = _cycle(db_session, publisher, now=NOW + timedelta(minutes=10))
    assert len(publisher.sent) == 1
    assert second["skipped"]


def test_minimum_interval_between_publications_is_enforced(db_session, source):
    _configure(
        db_session,
        approved_source_ids=[source.id],
        min_interval_seconds=3600,
        daily_publish_limit=10,
    )
    _approved_opportunity(db_session, source, "pub-interval-1")
    _approved_opportunity(db_session, source, "pub-interval-2", SECOND_JOB_POST)

    publisher = FakePublisher()
    _cycle(db_session, publisher)
    assert len(publisher.sent) == 1

    _cycle(db_session, publisher, now=NOW + timedelta(minutes=10))
    assert len(publisher.sent) == 1

    _cycle(db_session, publisher, now=NOW + timedelta(hours=2))
    assert len(publisher.sent) == 2


def test_duplicate_content_is_not_published_twice(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-dup-1")

    publisher = FakePublisher()
    _cycle(db_session, publisher)
    assert len(publisher.sent) == 1

    # The same content collected from another post must not be published again.
    from app.services.ingestion import ingest_post

    duplicate, _ = ingest_post(
        db_session,
        source_id=source.id,
        external_id="pub-dup-2",
        original_text=JOB_POST,
    )
    evaluate_opportunity(db_session, duplicate, provider=StubProvider())
    db_session.commit()

    _cycle(db_session, publisher, now=NOW + timedelta(minutes=5))
    assert len(publisher.sent) == 1


def test_running_the_cycle_twice_does_not_publish_twice(db_session, source):
    """A worker restart must not create a second message."""
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-restart")

    publisher = FakePublisher()
    _cycle(db_session, publisher)
    _cycle(db_session, publisher, now=NOW + timedelta(minutes=1))

    assert len(publisher.sent) == 1
    assert (
        db_session.scalar(
            select(Publication).where(Publication.opportunity_id.is_not(None))
        )
        is not None
    )


def test_uncertain_sending_state_is_not_blindly_resent(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    opportunity = _approved_opportunity(db_session, source, "pub-sending")

    publication = Publication(
        opportunity_id=opportunity.id,
        channel="target",
        status="sending",
        attempts=1,
        created_by="system:autonomy",
        is_autonomous=True,
        content_hash=opportunity.post.content_hash,
    )
    db_session.add(publication)
    db_session.commit()

    publisher = FakePublisher()
    outcome = _cycle(db_session, publisher)

    assert publisher.sent == []
    assert outcome["published"] == []
    db_session.refresh(publication)
    assert publication.status == "sending"


# --- Cancellation and failure ---------------------------------------------


def test_scheduled_autonomous_publication_is_cancelled_after_rejection(
    db_session, source
):
    from app.services.publishing import publish_publication

    _configure(db_session, approved_source_ids=[source.id])
    opportunity = _approved_opportunity(db_session, source, "pub-cancel")

    publication = Publication(
        opportunity_id=opportunity.id,
        channel="target",
        status="scheduled",
        attempts=0,
        created_by="system:autonomy",
        is_autonomous=True,
        autonomy_mode="production",
        content_hash=opportunity.post.content_hash,
    )
    db_session.add(publication)
    db_session.commit()

    opportunity.review_status = "rejected"
    db_session.commit()

    publisher = FakePublisher()
    with pytest.raises(PublishingError):
        asyncio.run(
            publish_publication(
                db_session,
                publication,
                publisher=publisher,
                settings=_settings(),
                now=NOW,
            )
        )

    assert publisher.sent == []
    db_session.refresh(publication)
    assert publication.status == "cancelled"


def test_failed_send_is_recorded_and_does_not_republish_immediately(
    db_session, source
):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-failed")

    publisher = FakePublisher()
    publisher.fail_with = RuntimeError("network down")
    publisher.fail_times = 1

    outcome = _cycle(db_session, publisher)

    assert outcome["published"] == []
    publication = db_session.scalar(select(Publication))
    assert publication.status == "failed"
    assert publication.attempts == 1


def test_publication_decisions_are_recorded(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-audit")

    _cycle(db_session, FakePublisher())

    decisions = db_session.scalars(
        select(AutonomyDecision).where(AutonomyDecision.decision == "auto_published")
    ).all()
    assert len(decisions) == 1
    assert decisions[0].actor == "system:autonomy"


def test_publishing_requires_publishing_enabled(db_session, source):
    _configure(db_session, approved_source_ids=[source.id])
    _approved_opportunity(db_session, source, "pub-disabled-flag")

    publisher = FakePublisher()
    outcome = _cycle(
        db_session, publisher, settings=_settings(publishing_enabled=False)
    )

    assert publisher.sent == []
    assert outcome["skipped"] == ["publishing is disabled"]


def test_configuration_source_is_reported(db_session):
    _configure(db_session)
    config = resolve_config(db_session)
    assert config.source == "database"
    assert config.publishes is True
    assert config.channel == "@target"


def test_opportunity_records_keep_their_classification(db_session, source):
    """Published records remain traceable to the classifier that produced them."""
    _configure(db_session, approved_source_ids=[source.id])
    opportunity = _approved_opportunity(db_session, source, "pub-trace")

    assert opportunity.classification == "freelance_job"
    assert opportunity.classifier_version
    assert opportunity.model_name == "stub-model"
    assert opportunity.classification_evidence
