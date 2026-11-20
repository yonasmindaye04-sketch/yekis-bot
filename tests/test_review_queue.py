"""The review queue must report the backlog that actually exists.

The dashboard shows the size of the review backlog next to the tab, and the
operator decides whether to open the queue from that number. The number was
taken from the rows of the current page, under the current filter, so a
`pending_review` filter that matched nothing reported zero while hundreds of
posts sat in `needs_review` waiting for a decision.

These tests pin the three parts of that behaviour:

* the summary counts every review queue, not just the filtered page;
* the summary excludes what cannot be reviewed any more (published, closed);
* the created date range filters on the collection date, inclusive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import CollectedPost, Opportunity


def _source_id(client) -> int:
    response = client.post(
        "/api/v1/sources",
        json={
            "name": "Queue source",
            "source_type": "manual",
            "source_url": "https://t.me/queue_source",
            "is_active": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _ingest(client, source_id: int, external_id: str, text: str) -> dict:
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source_id,
            "external_id": external_id,
            "original_text": text,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_status(db_session, opportunity_id: int, status: str) -> None:
    opportunity = db_session.get(Opportunity, opportunity_id)
    opportunity.review_status = status
    db_session.commit()


def _set_created_at(db_session, opportunity_id: int, value: datetime) -> None:
    opportunity = db_session.get(Opportunity, opportunity_id)
    opportunity.created_at = value
    db_session.commit()


PUBLISHABLE = "Looking for a React developer. Budget $300. Delivery in 2 weeks."
UNCLEAR = "Monitoring and Evaluation Officer\nAddis Ababa\ndeadline: May 23, 2019"


def test_queue_summary_counts_the_whole_backlog(client, db_session):
    source_id = _source_id(client)
    _ingest(client, source_id, "q-1", PUBLISHABLE)
    _ingest(client, source_id, "q-2", UNCLEAR)
    approved = _ingest(client, source_id, "q-3", PUBLISHABLE)
    rejected = _ingest(client, source_id, "q-4", PUBLISHABLE)

    _set_status(db_session, approved["id"], "approved")
    _set_status(db_session, rejected["id"], "rejected")

    summary = client.get("/api/v1/opportunities/queue-summary").json()

    assert summary["pending_review"] + summary["needs_review"] == 2
    assert summary["actionable"] == 2
    assert summary["approved"] == 1
    assert summary["rejected"] == 1
    assert summary["total"] >= 4

    # The empty-filter trap: the pending queue alone is not the backlog.
    only_pending = client.get("/api/v1/opportunities?status=pending_review").json()
    actionable = client.get("/api/v1/opportunities?status=actionable").json()
    assert summary["actionable"] == len(actionable)
    assert summary["pending_review"] == len(only_pending)


def test_queue_summary_is_independent_of_the_page_size(client):
    source_id = _source_id(client)
    for index in range(3):
        _ingest(client, source_id, f"page-{index}", UNCLEAR)

    summary = client.get("/api/v1/opportunities/queue-summary").json()
    page = client.get("/api/v1/opportunities?status=actionable&limit=1").json()

    assert len(page) == 1
    # The page is capped, the summary is not.
    assert summary["actionable"] >= 3


def test_queue_summary_excludes_published_and_closed(client, db_session):
    source_id = _source_id(client)
    published = _ingest(client, source_id, "pub-1", PUBLISHABLE)
    closed = _ingest(client, source_id, "closed-1", PUBLISHABLE)
    waiting = _ingest(client, source_id, "waiting-1", UNCLEAR)

    from app.models import Publication

    db_session.add(
        Publication(
            opportunity_id=published["id"],
            channel="target_channel",
            status="published",
            created_by="test-admin",
        )
    )
    opportunity = db_session.get(Opportunity, closed["id"])
    opportunity.classification = "closed_or_filled_job"
    opportunity.review_status = "rejected"
    db_session.commit()

    summary = client.get("/api/v1/opportunities/queue-summary").json()

    # The published and the closed row are not in the review backlog.
    assert summary["pending_review"] + summary["needs_review"] == 1
    assert summary["actionable"] == 1
    assert summary["published"] == 1
    assert summary["closed"] == 1
    assert summary["rejected"] == 0

    ids = {
        item["id"]
        for item in client.get("/api/v1/opportunities?status=actionable").json()
    }
    assert ids == {waiting["id"]}


def test_created_date_range_filters_inclusively(client, db_session):
    source_id = _source_id(client)
    fresh = _ingest(client, source_id, "range-new", UNCLEAR)
    old = _ingest(client, source_id, "range-old", UNCLEAR)

    today = datetime.now(UTC)
    _set_created_at(db_session, fresh["id"], today)
    _set_created_at(db_session, old["id"], today - timedelta(days=30))

    day = today.date().isoformat()
    in_range = client.get(
        f"/api/v1/opportunities?status=all&created_from={day}&created_to={day}"
    ).json()
    ids = {item["id"] for item in in_range}
    assert fresh["id"] in ids
    assert old["id"] not in ids

    # A single day bound works on its own, and the range includes the whole
    # of the end day rather than stopping at midnight.
    from_only = client.get(
        f"/api/v1/opportunities?status=all&created_from={day}"
    ).json()
    assert fresh["id"] in {item["id"] for item in from_only}

    old_day = (today - timedelta(days=30)).date().isoformat()
    to_old = client.get(
        f"/api/v1/opportunities?status=all&created_to={old_day}"
    ).json()
    old_ids = {item["id"] for item in to_old}
    assert old["id"] in old_ids
    assert fresh["id"] not in old_ids


def test_the_collection_time_is_recorded(client, db_session):
    """The date filter reads the stored collection time, not the post date."""
    source_id = _source_id(client)
    created = _ingest(client, source_id, "stamp-1", UNCLEAR)
    opportunity = db_session.get(Opportunity, created["id"])
    post = db_session.get(CollectedPost, opportunity.collected_post_id)
    assert post.collected_at is not None
    assert opportunity.created_at is not None
