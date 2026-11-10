from datetime import UTC, datetime

from app.models import Publication
from app.services.ingestion import ingest_post

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _seed(db_session, source):
    approved, _ = ingest_post(
        db_session,
        source_id=source.id,
        external_id="analytics-1",
        original_text=(
            "Looking for a React developer to build a dashboard. "
            "Must know TypeScript. Budget $300. Delivery in 2 weeks. Remote."
        ),
    )
    rejected, _ = ingest_post(
        db_session,
        source_id=source.id,
        external_id="analytics-2",
        original_text="Need a graphic designer. Budget ETB 15,000. 3 days.",
    )
    flagged, _ = ingest_post(
        db_session,
        source_id=source.id,
        external_id="analytics-3",
        original_text=(
            "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job."
        ),
    )
    approved.review_status = "approved"
    approved.reviewed_at = NOW
    approved.reviewed_by = "test-reviewer"
    rejected.review_status = "rejected"
    rejected.reviewed_at = NOW
    rejected.reviewed_by = "test-reviewer"
    db_session.commit()

    db_session.add(
        Publication(
            opportunity_id=approved.id,
            channel="audit_publish",
            status="published",
            external_message_id="42",
            published_at=NOW,
            attempts=1,
            created_by="test-reviewer",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db_session.commit()
    return approved, rejected, flagged


def test_overview_counts_and_statuses(db_session, source, analyst_client):
    _seed(db_session, source)

    response = analyst_client.get("/api/v1/analytics/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_sources"] == 1
    assert body["active_sources"] == 1
    assert body["total_posts"] == 3
    assert body["total_opportunities"] == 3
    assert body["by_status"]["approved"] == 1
    assert body["by_status"]["rejected"] == 1
    assert body["by_status"]["needs_review"] == 1
    assert body["risk_flagged"] == 1
    assert body["published"] == 1
    assert body["publication_failures"] == 0
    assert body["approval_rate"] == 50.0
    assert body["last_collected_at"] is not None


def test_source_quality_rollup(db_session, source, analyst_client):
    _seed(db_session, source)

    response = analyst_client.get("/api/v1/analytics/sources")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == source.id
    assert row["posts"] == 3
    assert row["opportunities"] == 3
    assert row["approved"] == 1
    assert row["rejected"] == 1
    assert row["needs_review"] == 1
    assert row["risk_flagged"] == 1
    assert row["approval_rate"] == 50.0
    assert row["published"] == 1


def test_market_insights_skills_budget_and_remote(db_session, source, analyst_client):
    _seed(db_session, source)

    response = analyst_client.get("/api/v1/analytics/market")
    assert response.status_code == 200, response.text
    body = response.json()
    skills = {item["key"]: item["count"] for item in body["skills_demand"]}
    assert skills["react"] == 1
    assert skills["graphic design"] == 1
    budgets = {item["currency"]: item for item in body["budget"]}
    assert budgets["USD"]["count"] == 1
    assert budgets["USD"]["avg_amount"] == 300.0
    assert budgets["ETB"]["avg_amount"] == 15000.0
    # Remote share is among opportunities with a known remote flag; two are
    # explicitly remote here and the design brief is unknown.
    assert body["remote_share"] == 100.0


def test_trends_include_posts_reviews_and_publications(db_session, source, analyst_client):
    _seed(db_session, source)

    response = analyst_client.get("/api/v1/analytics/trends?days=30")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) >= 1
    totals = {
        "posts": sum(row["posts"] for row in rows),
        "opportunities": sum(row["opportunities"] for row in rows),
        "approved": sum(row["approved"] for row in rows),
        "published": sum(row["published"] for row in rows),
    }
    assert totals == {"posts": 3, "opportunities": 3, "approved": 1, "published": 1}


def test_trends_window_is_bounded(analyst_client):
    assert analyst_client.get("/api/v1/analytics/trends?days=0").status_code == 422
    assert analyst_client.get("/api/v1/analytics/trends?days=366").status_code == 422


def test_analytics_requires_analyst_or_reviewer(anon_client, collector_client, analyst_client):
    assert anon_client.get("/api/v1/analytics/overview").status_code == 401
    assert collector_client.get("/api/v1/analytics/overview").status_code == 403
    assert analyst_client.get("/api/v1/analytics/overview").status_code == 200
