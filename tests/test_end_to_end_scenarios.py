"""Phase L — End-to-End System Acceptance Scenarios.

Verifies the 10 required end-to-end integration scenarios for Yekis Intelligence:
- Scenario 1: Complete ingestion pipeline
- Scenario 2: Duplicate ingestion prevention & idempotency
- Scenario 3: Risky opportunity detection & suppression
- Scenario 4: Review workflow & append-only audit trail
- Scenario 5: Role-based access control restrictions
- Scenario 6: Approval-gated publishing with attribution
- Scenario 7: Scheduled publication cancellation on rejection
- Scenario 8: Worker failure recovery & state preservation
- Scenario 9: LLM failure fallback to deterministic rules
- Scenario 10: Cross-system analytics data consistency
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from sqlalchemy import func, select

from app.integrations.telegram.publisher import FakePublisher
from app.models import CollectedPost, Opportunity
from app.security.auth import Principal
from app.services.jobs import run_job
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.orchestrator import extract_with_provider
from app.services.publishing import publish_due, publish_opportunity


def _create_source(client, name="Scenario Test Channel", **overrides):
    payload = {
        "name": name,
        "source_type": "telegram",
        "source_url": "https://t.me/scenario_test_channel",
        "category": "software",
        "is_active": True,
    }
    payload.update(overrides)
    res = client.post("/api/v1/sources", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


# ==============================================================================
# Scenario 1: Complete Ingestion
# ==============================================================================
def test_scenario_1_complete_ingestion(client, db_session):
    """Test 1: Complete ingestion flow from source creation to pending review."""
    src = _create_source(client, name="Freelance Tech Board")

    payload = {
        "source_id": src["id"],
        "external_id": "scen-001",
        "original_text": (
            "We are seeking a Full Stack React and Python developer. "
            "Experience with FastAPI and Tailwind is required. "
            "Budget is $1,200 USD. Project deadline is 3 weeks."
        ),
        "original_url": "https://t.me/scenario_test_channel/1",
    }
    res = client.post("/api/v1/ingestion/manual", json=payload)
    assert res.status_code == 201, res.text
    data = res.json()

    # Verification: Opportunity extracted, classified, quality scored, pending review
    assert data["opportunity_type"] == "freelance_job"
    assert "react" in [s.lower() for s in data["skills"]]
    assert "python" in [s.lower() for s in data["skills"]]
    assert data["budget_amount"] == 1200.0
    assert data["budget_currency"] == "USD"
    assert data["quality_score"] > 0
    assert data["risk_score"] == 0
    assert data["risk_flags"] == []
    assert data["review_status"] == "pending_review"

    # Verification: exactly one opportunity and one preserved original post
    opp_count = db_session.scalar(select(func.count()).select_from(Opportunity))
    post_count = db_session.scalar(select(func.count()).select_from(CollectedPost))
    assert opp_count == 1
    assert post_count == 1

    post_stmt = select(CollectedPost).where(CollectedPost.id == data["collected_post_id"])
    post = db_session.scalar(post_stmt)
    assert post is not None
    assert post.original_text == payload["original_text"]
    assert post.external_id == "scen-001"


# ==============================================================================
# Scenario 2: Duplicate Ingestion
# ==============================================================================
def test_scenario_2_duplicate_ingestion(client, db_session):
    """Test 2: Re-ingesting the same external message does not create duplicates."""
    src = _create_source(client)
    payload = {
        "source_id": src["id"],
        "external_id": "scen-dup-002",
        "original_text": "Need a senior backend engineer. Pay $1,500 USD.",
    }

    # First ingestion succeeds
    first = client.post("/api/v1/ingestion/manual", json=payload)
    assert first.status_code == 201

    # Second ingestion of identical message fails cleanly with HTTP 409
    dup = client.post("/api/v1/ingestion/manual", json=payload)
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "duplicate_post"

    # Check database: exactly one record exists
    opp_count = db_session.scalar(select(func.count()).select_from(Opportunity))
    post_count = db_session.scalar(select(func.count()).select_from(CollectedPost))
    assert opp_count == 1
    assert post_count == 1


# ==============================================================================
# Scenario 3: Risky Opportunity
# ==============================================================================
def test_scenario_3_risky_opportunity(client, db_session):
    """Test 3: Ingesting an upfront fee scam flags review and suppresses bait budget."""
    src = _create_source(client)
    scam_text = "Pay ETB 500 registration fee to receive a remote job paying USD 5,000."
    payload = {
        "source_id": src["id"],
        "external_id": "scen-scam-003",
        "original_text": scam_text,
    }

    res = client.post("/api/v1/ingestion/manual", json=payload)
    assert res.status_code == 201
    data = res.json()

    # Original text preserved verbatim
    post_stmt = select(CollectedPost).where(CollectedPost.id == data["collected_post_id"])
    post = db_session.scalar(post_stmt)
    assert post.original_text == scam_text

    # Upfront payment risk detected, routed to needs_review, bait budget suppressed
    assert data["review_status"] == "needs_review"
    assert "upfront_payment" in data["risk_flags"]
    assert data["risk_score"] > 0
    assert data["budget_amount"] is None  # Bait budget is NOT treated as verified earnings
    assert data["review_status"] != "approved"  # Never auto-approved


# ==============================================================================
# Scenario 4: Review Workflow
# ==============================================================================
def test_scenario_4_review_workflow(client, reviewer_client, db_session):
    """Test 4: Reviewer inspects, edits fields, approves, and checks append-only audit trail."""
    src = _create_source(client)
    sample_desc = "Need a graphic designer for mobile app illustrations. Budget $400 USD."
    ingested = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "scen-rev-004",
            "original_text": sample_desc,
        },
    ).json()
    opp_id = ingested["id"]

    # 1. Edit an extracted field as reviewer
    edit_res = reviewer_client.patch(
        f"/api/v1/opportunities/{opp_id}",
        json={"title": "Senior Mobile UI/UX Illustrator", "category": "design"},
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["title"] == "Senior Mobile UI/UX Illustrator"

    # 2. Approve the opportunity with reviewer note
    appr_res = reviewer_client.patch(
        f"/api/v1/opportunities/{opp_id}/review",
        json={"decision": "approved", "reviewer_note": "Verified client legitimacy and rate."},
    )
    assert appr_res.status_code == 200
    assert appr_res.json()["review_status"] == "approved"
    assert appr_res.json()["reviewed_by"] == "test-reviewer"

    # 3. Inspect audit events
    events_res = reviewer_client.get(f"/api/v1/opportunities/{opp_id}/events")
    assert events_res.status_code == 200
    events = events_res.json()
    assert len(events) >= 2

    # Events contain edit and status change decisions with actor identity
    event_types = [e["event_type"] for e in events]
    assert "field_edit" in event_types
    assert "status_change" in event_types
    assert any(e["note"] == "Verified client legitimacy and rate." for e in events)


# ==============================================================================
# Scenario 5: Role Restrictions
# ==============================================================================
def test_scenario_5_role_restrictions(
    client, reviewer_client, collector_client, analyst_client, anon_client
):
    """Test 5: Strict RBAC enforcement across all protected endpoints."""
    src = _create_source(client)
    opp = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "scen-rbac-005",
            "original_text": "Need a copywriter. Budget $200 USD.",
        },
    ).json()
    opp_id = opp["id"]

    # Collector cannot approve content -> 403 Forbidden
    col_review = collector_client.patch(
        f"/api/v1/opportunities/{opp_id}/review",
        json={"decision": "approved"},
    )
    assert col_review.status_code == 403
    assert col_review.json()["detail"]["code"] == "insufficient_role"

    # Analyst cannot ingest content -> 403 Forbidden
    ana_ingest = analyst_client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "scen-rbac-005b",
            "original_text": "Need an editor.",
        },
    )
    assert ana_ingest.status_code == 403
    assert ana_ingest.json()["detail"]["code"] == "insufficient_role"

    # Unauthenticated user cannot access protected resources -> 401 Unauthorized
    anon_sources = anon_client.get("/api/v1/sources")
    assert anon_sources.status_code == 401
    assert anon_sources.json()["detail"]["code"] == "authentication_required"

    anon_opps = anon_client.get("/api/v1/opportunities")
    assert anon_opps.status_code == 401


# ==============================================================================
# Scenario 6: Publishing
# ==============================================================================
def test_scenario_6_publishing(client, reviewer_client, db_session):
    """Test 6: Approved opportunity published to an authorized destination with attribution."""
    src = _create_source(client)
    opp = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "scen-pub-006",
            "original_text": "Senior Rust Engineer needed for DeFi infrastructure. $5,000 USD.",
            "original_url": "https://t.me/scenario_test_channel/6",
        },
    ).json()
    opp_id = opp["id"]

    # Approve first
    reviewer_client.patch(
        f"/api/v1/opportunities/{opp_id}/review",
        json={"decision": "approved"},
    )

    fake_publisher = FakePublisher()
    settings = SimpleNamespace(
        publishing_enabled=True,
        publishing_allowed_channels=["@audit_channel"],
        publishing_max_attempts=3,
    )
    now = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)

    # Publish through publisher service
    pub = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opp_id,
            channel="@audit_channel",
            principal=Principal(name="test-reviewer", role="reviewer"),
            publisher=fake_publisher,
            settings=settings,
            now=now,
        )
    )

    assert pub.status == "published"
    assert pub.channel == "audit_channel"
    assert len(fake_publisher.sent) == 1

    sent_channel, sent_text = fake_publisher.sent[0]
    assert sent_channel == "audit_channel"
    assert "Source: Scenario Test Channel" in sent_text
    assert "https://t.me/scenario_test_channel" in sent_text


# ==============================================================================
# Scenario 7: Scheduled Publication Cancellation
# ==============================================================================
def test_scenario_7_scheduled_publication_cancellation(client, reviewer_client, db_session):
    """Test 7: Rejecting a scheduled opportunity prevents its broadcast at send time."""
    src = _create_source(client)
    opp = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "scen-sched-007",
            "original_text": "DevOps Engineer contract. Budget $3,000 USD.",
        },
    ).json()
    opp_id = opp["id"]

    # 1. Approve opportunity
    reviewer_client.patch(
        f"/api/v1/opportunities/{opp_id}/review",
        json={"decision": "approved"},
    )

    now = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    scheduled_time = now + timedelta(hours=2)
    fake_publisher = FakePublisher()
    settings = SimpleNamespace(
        publishing_enabled=True,
        publishing_allowed_channels=["@audit_channel"],
        publishing_max_attempts=3,
    )

    # 2. Schedule publication
    pub = asyncio.run(
        publish_opportunity(
            db_session,
            opportunity_id=opp_id,
            channel="@audit_channel",
            principal=Principal(name="test-reviewer", role="reviewer"),
            publisher=fake_publisher,
            settings=settings,
            scheduled_at=scheduled_time,
            now=now,
        )
    )
    assert pub.status == "scheduled"
    assert len(fake_publisher.sent) == 0

    # 3. Reviewer rejects the opportunity before scheduled time
    reviewer_client.patch(
        f"/api/v1/opportunities/{opp_id}/review",
        json={"decision": "rejected", "reviewer_note": "Spam reported after approval"},
    )

    # 4. Beat scheduler triggers at scheduled time
    due_results = asyncio.run(
        publish_due(
            db_session,
            publisher=fake_publisher,
            settings=settings,
            now=scheduled_time + timedelta(minutes=5),
        )
    )

    # Verify: Publication was NOT sent, due_results is empty, and stays scheduled (skipped)
    assert len(fake_publisher.sent) == 0
    assert due_results == []
    db_session.refresh(pub)
    assert pub.status == "scheduled"


# ==============================================================================
# Scenario 8: Worker Recovery
# ==============================================================================
def test_scenario_8_worker_recovery(db_session, source):
    """Test 8: Background job executes durably and prevents duplicate work on recovery."""
    calls = {"count": 0}

    def flaky_task():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("Transient worker interruption or network reset")
        return {"result": "success", "processed_at": 123}

    # Attempt 1 & 2: run_job retries and succeeds on attempt 2
    outcome = run_job(
        db_session,
        idempotency_key="scen-recovery-008",
        task_name="yekis.test_recovery",
        fn=flaky_task,
        max_attempts=3,
        base_seconds=1,
        max_seconds=10,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    assert outcome.status == "succeeded"
    assert outcome.attempts == 2

    # Attempt 3: Subsequent rerun returns stored result without running task again
    outcome_replay = run_job(
        db_session,
        idempotency_key="scen-recovery-008",
        task_name="yekis.test_recovery",
        fn=flaky_task,
    )
    assert outcome_replay.status == "succeeded"
    assert calls["count"] == 2  # Task was not re-executed


# ==============================================================================
# Scenario 9: LLM Failure Fallback
# ==============================================================================
def test_scenario_9_llm_failure_fallback():
    """Test 9: LLM timeout or provider error gracefully degrades to deterministic rules."""
    def timeout_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Provider request timed out after 30s")

    mock_client = httpx.Client(transport=httpx.MockTransport(timeout_transport))
    provider = OpenAICompatibleProvider(
        api_key="mock-key-1234567890",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        client=mock_client,
    )

    extracted, extraction_method = extract_with_provider(
        "Looking for a Python Django developer to build an API. Budget is $800 USD.",
        provider=provider,
    )

    # Verification: Extracted without crashing; recorded extraction_method is rules_fallback
    assert extraction_method == "rules_fallback"
    assert extracted.opportunity_type == "freelance_job"
    assert "python" in [s.lower() for s in extracted.skills]
    assert extracted.budget_amount == 800.0
    assert extracted.budget_currency == "USD"


# ==============================================================================
# Scenario 10: Analytics Consistency
# ==============================================================================
def test_scenario_10_analytics_consistency(client, reviewer_client, analyst_client, db_session):
    """Test 10: Cross-system analytics accurately mirror database ground truth."""
    src = _create_source(client, name="Analytics Source 1")

    # Seed 3 posts
    p1 = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "an-1",
            "original_text": "Python engineer. Budget $1,000 USD.",
        },
    ).json()
    client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "an-2",
            "original_text": "React designer. Budget $500 USD.",
        },
    )
    client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": src["id"],
            "external_id": "an-3",
            "original_text": "Pay 500 registration fee upfront scam.",
        },
    )

    # Approve 1 opportunity
    reviewer_client.patch(
        f"/api/v1/opportunities/{p1['id']}/review",
        json={"decision": "approved"},
    )

    # Query analytics overview as analyst
    overview = analyst_client.get("/api/v1/analytics/overview").json()
    assert overview["total_sources"] == 1
    assert overview["active_sources"] == 1
    assert overview["total_posts"] == 3
    assert overview["total_opportunities"] == 3
    assert overview["by_status"]["approved"] == 1
    assert overview["by_status"]["pending_review"] == 1
    assert overview["by_status"]["needs_review"] == 1
    assert overview["risk_flagged"] == 1

    # Query market analytics
    market = analyst_client.get("/api/v1/analytics/market").json()
    assert any(s["key"] == "python" for s in market["skills_demand"])
    usd_stats = next((b for b in market["budget"] if b["currency"] == "USD"), None)
    assert usd_stats is not None
    assert usd_stats["count"] == 2
    assert usd_stats["min_amount"] == 500.0
    assert usd_stats["max_amount"] == 1000.0
