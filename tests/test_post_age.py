"""Old posts must never enter the queue.

The channels carry years of history. A post that was published years ago is
not an opportunity anybody can act on, so it is never collected, and the
posts that were already collected are closed by the stale sweep once their
real publication date is known.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import CollectedPost, Opportunity, ReviewEvent, Source
from app.services.autonomy import sweep_stale_opportunities
from app.services.ingestion import PostTooOldError, ingest_post


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _source(db, name: str = "age source") -> Source:
    source = Source(name=name, source_type="telegram", source_url="https://t.me/age_src")
    db.add(source)
    db.commit()
    return source


def test_a_post_older_than_the_limit_is_not_stored(db):
    source = _source(db)
    old = datetime.now(UTC) - timedelta(days=400)
    with pytest.raises(PostTooOldError):
        ingest_post(
            db,
            source_id=source.id,
            external_id="1",
            original_text="Looking for a React developer.",
            published_at=old,
            max_age_days=30,
        )
    assert db.scalar(select(CollectedPost).where(CollectedPost.external_id == "1")) is None


def test_a_fresh_post_is_stored_with_its_publication_date(db):
    source = _source(db)
    posted = datetime.now(UTC) - timedelta(days=2)
    opportunity, created = ingest_post(
        db,
        source_id=source.id,
        external_id="2",
        original_text="Looking for a React developer for a two week project.",
        published_at=posted,
        max_age_days=30,
    )
    assert created is True
    post = db.get(CollectedPost, opportunity.collected_post_id)
    assert post is not None
    assert post.published_at is not None
    assert post.published_at.replace(tzinfo=UTC) == posted


def test_no_limit_keeps_every_post(db):
    source = _source(db)
    old = datetime.now(UTC) - timedelta(days=400)
    opportunity, created = ingest_post(
        db,
        source_id=source.id,
        external_id="3",
        original_text="Looking for a React developer.",
        published_at=old,
        max_age_days=0,
    )
    assert created is True


def test_the_stale_sweep_closes_posts_older_than_the_window(db):
    source = _source(db)
    old = datetime.now(UTC) - timedelta(days=400)
    recent = datetime.now(UTC) - timedelta(days=2)
    for index, published in enumerate((old, recent)):
        opportunity, _ = ingest_post(
            db,
            source_id=source.id,
            external_id=str(10 + index),
            original_text=f"Looking for a developer number {index}.",
            published_at=published,
        )
        opportunity.review_status = "needs_review"
    db.commit()

    class _Settings:
        autonomy_stale_after_hours = 24 * 30

    closed = sweep_stale_opportunities(db, settings=_Settings())
    assert closed == [1]

    rows = db.scalars(select(Opportunity).order_by(Opportunity.id)).all()
    assert rows[0].review_status == "rejected"
    assert "too old" in (rows[0].approval_reason or "")
    assert rows[1].review_status == "needs_review"


def test_the_stale_sweep_also_closes_posts_that_were_evaluated(db):
    """A post that a human decision already touched is still stale.

    The sweep used to skip every post that had an approval method, which is
    every post the autonomy cycle had already looked at. That left the whole
    backlog in the queue forever.
    """
    source = _source(db)
    old = datetime.now(UTC) - timedelta(days=400)
    opportunity, _ = ingest_post(
        db,
        source_id=source.id,
        external_id="20",
        original_text="Looking for a developer.",
        published_at=old,
    )
    opportunity.review_status = "needs_review"
    opportunity.approval_method = "human_review"
    db.commit()

    class _Settings:
        autonomy_stale_after_hours = 24 * 30

    closed = sweep_stale_opportunities(db, settings=_Settings())
    assert closed == [opportunity.id]
    assert db.get(Opportunity, opportunity.id).review_status == "rejected"
    events = db.scalars(
        select(ReviewEvent).where(ReviewEvent.opportunity_id == opportunity.id)
    ).all()
    assert events and events[0].event_type == "auto_rejection"
