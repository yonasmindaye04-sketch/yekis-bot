"""Autonomous approval tests.

Covers the three decision levels of the autonomy engine: automatic approval,
human review, and automatic rejection or quarantine. Every automatic decision
must be traceable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import AutonomyDecision, Opportunity, ReviewEvent
from app.services.autonomy import (
    evaluate_opportunity,
    overview,
    recent_decisions,
    resolve_config,
    update_config,
)
from app.services.classification.schema import AIPostClassification

JOB_POST = (
    "Looking for a React developer to build a dashboard. Must know TypeScript "
    "and Tailwind. Budget $300. Delivery in 2 weeks. Please send your portfolio."
)
EDUCATION_POST = (
    "In between job opportunities, we will send you some tips to improve your "
    "chances to land a job. Today part 4 of skills you need to have."
)
SCAM_POST = (
    "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job."
)


class StubProvider:
    """A model that always agrees with the deterministic classifier."""

    name = "llm"
    model = "stub-model"

    def __init__(self, classification: str = "freelance_job", confidence: float = 0.98):
        self.classification = classification
        self.confidence = confidence

    def classify(self, text: str) -> AIPostClassification:
        publishable = self.classification in (
            "freelance_job",
            "project_contract",
            "remote_contract",
            "consulting_opportunity",
            "short_term_contract",
        )
        return AIPostClassification.model_validate(
            {
                "classification": self.classification,
                "job_category": "software",
                "engagement_type": self.classification if publishable else None,
                "is_genuine_opportunity": publishable,
                "is_publishable": publishable,
                "confidence": self.confidence,
                "title": None,
                "skills": [],
                "budget": None,
                "currency": None,
                "duration": None,
                "deadline": None,
                "client_name": None,
                "application_url": None,
                "risk_level": "none",
                "risk_flags": [],
                "evidence": ["the model agrees with the rule engine"],
                "rejection_reasons": [],
            }
        )


def _ingest(db, source, external_id: str, text: str) -> Opportunity:
    from app.services.ingestion import ingest_post

    opportunity, _ = ingest_post(
        db, source_id=source.id, external_id=external_id, original_text=text
    )
    db.refresh(opportunity)
    return opportunity


def _enable(db, **overrides):
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
    return update_config(db, values, actor="test-admin")


# --- Configuration ---------------------------------------------------------


def test_autonomy_is_disabled_by_default(db_session):
    config = resolve_config(db_session)
    assert config.enabled is False
    assert config.mode == "shadow"
    assert not config.publishes
    assert config.channel is None


def test_settings_can_be_updated_and_are_read_back(db_session):
    _enable(db_session, auto_approval_threshold=0.9, daily_publish_limit=3)
    config = resolve_config(db_session)
    assert config.enabled is True
    assert config.mode == "production"
    assert config.threshold == 0.9
    assert config.daily_publish_limit == 3
    assert config.source == "database"


def test_invalid_threshold_is_rejected(db_session):
    with pytest.raises(ValueError):
        update_config(db_session, {"auto_approval_threshold": 0.1}, actor="test-admin")


def test_invalid_mode_is_rejected(db_session):
    with pytest.raises(ValueError):
        update_config(db_session, {"mode": "yolo"}, actor="test-admin")


# --- Shadow mode -----------------------------------------------------------


def test_shadow_mode_records_the_decision_without_approving(db_session, source):
    _enable(db_session, mode="shadow", approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "shadow-1", JOB_POST)

    outcome = evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    assert outcome.decision == "would_auto_approve"
    assert outcome.applied is False
    db_session.refresh(opportunity)
    assert opportunity.review_status == "pending_review"
    assert opportunity.approval_method is None

    decisions = recent_decisions(db_session)
    assert len(decisions) == 1
    assert decisions[0].decision == "would_auto_approve"
    assert decisions[0].mode == "shadow"


def test_shadow_mode_records_rejections_too(db_session, source):
    _enable(db_session, mode="shadow", approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "shadow-2", EDUCATION_POST)

    outcome = evaluate_opportunity(
        db_session,
        opportunity,
        provider=StubProvider(classification="educational_content"),
    )

    assert outcome.decision == "would_auto_reject"
    db_session.refresh(opportunity)
    assert opportunity.review_status == "rejected"  # the classifier rejected it
    assert opportunity.approval_method == "auto"


# --- Automatic approval ----------------------------------------------------


def test_eligible_opportunity_is_approved_automatically(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "auto-1", JOB_POST)

    outcome = evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    assert outcome.decision == "auto_approved"
    assert outcome.applied is True
    db_session.refresh(opportunity)
    assert opportunity.review_status == "approved"
    assert opportunity.approval_method == "auto"
    assert opportunity.approved_by == "system:autonomy"
    assert opportunity.approved_at is not None
    assert opportunity.classifier_version
    assert opportunity.model_name == "stub-model"
    assert opportunity.classification_confidence >= 0.95

    events = db_session.scalars(
        select(ReviewEvent).where(ReviewEvent.opportunity_id == opportunity.id)
    ).all()
    assert [event.event_type for event in events] == ["auto_approval"]
    assert events[0].actor == "system:autonomy"

    decisions = recent_decisions(db_session)
    assert decisions[0].decision == "auto_approved"


def test_disabled_autonomy_prevents_new_automatic_approvals(db_session, source):
    _enable(db_session, enabled=False, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "auto-2", JOB_POST)

    outcome = evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    assert outcome.decision == "would_auto_approve"
    assert outcome.applied is False
    db_session.refresh(opportunity)
    assert opportunity.review_status == "pending_review"


def test_low_confidence_requires_human_review(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "auto-3", JOB_POST)

    outcome = evaluate_opportunity(
        db_session, opportunity, provider=StubProvider(confidence=0.6)
    )

    assert outcome.decision == "human_review_required"
    db_session.refresh(opportunity)
    assert opportunity.review_status == "needs_review"
    assert opportunity.approval_method is None


def test_source_outside_the_allowlist_requires_human_review(db_session, source):
    _enable(db_session, approved_source_ids=[source.id + 999])
    opportunity = _ingest(db_session, source, "auto-4", JOB_POST)

    outcome = evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    assert outcome.decision == "human_review_required"
    db_session.refresh(opportunity)
    assert opportunity.review_status == "needs_review"


def test_category_outside_the_allowlist_requires_human_review(db_session, source):
    _enable(
        db_session, approved_source_ids=[source.id], approved_categories=["design"]
    )
    opportunity = _ingest(db_session, source, "auto-5", JOB_POST)

    outcome = evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    assert outcome.decision == "human_review_required"
    db_session.refresh(opportunity)
    assert opportunity.review_status == "needs_review"


def test_duplicate_content_requires_human_review(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    _ingest(db_session, source, "dup-a", JOB_POST)
    duplicate = _ingest(db_session, source, "dup-b", JOB_POST)

    outcome = evaluate_opportunity(db_session, duplicate, provider=StubProvider())

    assert outcome.decision == "human_review_required"
    db_session.refresh(duplicate)
    assert duplicate.review_status == "needs_review"


def test_high_risk_content_is_quarantined_and_never_approved(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "auto-6", SCAM_POST)

    outcome = evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    assert outcome.decision == "quarantined"
    db_session.refresh(opportunity)
    assert opportunity.review_status == "needs_review"
    assert opportunity.quarantined is True
    assert opportunity.approval_method == "auto"


def test_educational_content_is_never_approved(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "auto-7", EDUCATION_POST)

    evaluate_opportunity(db_session, opportunity, provider=StubProvider())

    db_session.refresh(opportunity)
    assert opportunity.review_status == "rejected"
    assert opportunity.is_publishable is False


# --- Audit trail -----------------------------------------------------------


def test_decision_log_is_append_only(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "audit-1", JOB_POST)

    evaluate_opportunity(db_session, opportunity, provider=StubProvider())
    first = recent_decisions(db_session)

    evaluate_opportunity(db_session, opportunity, provider=StubProvider())
    second = recent_decisions(db_session)

    assert len(second) == len(first) + 1
    assert second[0].id > first[0].id
    # The earlier rows are untouched.
    stored = db_session.get(AutonomyDecision, first[0].id)
    assert stored.decision == first[0].decision


def test_manual_override_of_an_automatic_approval_is_audited(db_session, source):
    from app.security.auth import Principal
    from app.services.review import apply_review

    _enable(db_session, approved_source_ids=[source.id])
    opportunity = _ingest(db_session, source, "audit-2", JOB_POST)
    evaluate_opportunity(db_session, opportunity, provider=StubProvider())
    db_session.refresh(opportunity)
    assert opportunity.review_status == "approved"

    apply_review(
        db_session,
        opportunity,
        decision="rejected",
        note="The client could not be verified.",
        principal=Principal(name="test-reviewer", role="reviewer"),
    )
    db_session.refresh(opportunity)

    assert opportunity.review_status == "rejected"
    assert opportunity.approval_method == "manual"
    events = db_session.scalars(
        select(ReviewEvent)
        .where(ReviewEvent.opportunity_id == opportunity.id)
        .order_by(ReviewEvent.id)
    ).all()
    assert [event.event_type for event in events] == ["auto_approval", "manual_override"]
    assert events[1].actor == "test-reviewer"


def test_reviewer_can_override_an_automatic_rejection(client, reviewer_client, source):
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source.id,
            "external_id": "override-1",
            "original_text": EDUCATION_POST,
        },
    )
    assert response.status_code == 201
    opportunity_id = response.json()["id"]
    assert response.json()["review_status"] == "rejected"

    override = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity_id}/review",
        json={"decision": "approved", "reviewer_note": "Verified manually."},
    )
    assert override.status_code == 200, override.text
    assert override.json()["review_status"] == "approved"

    events = client.get(f"/api/v1/opportunities/{opportunity_id}/events").json()
    assert any(event["event_type"] == "manual_override" for event in events)


# --- Overview and API ------------------------------------------------------


def test_overview_counts_every_decision_level(db_session, source):
    _enable(db_session, approved_source_ids=[source.id])
    approved = _ingest(db_session, source, "count-1", JOB_POST)
    evaluate_opportunity(db_session, approved, provider=StubProvider())
    _ingest(db_session, source, "count-2", SCAM_POST)

    counters = overview(db_session)
    assert counters["auto_approved"] == 1
    assert counters["quarantined"] == 1
    assert counters["decisions"] == 1


def test_autonomy_api_exposes_settings_overview_and_decisions(client, source):
    settings = client.get("/api/v1/autonomy/settings")
    assert settings.status_code == 200
    assert settings.json()["enabled"] is False
    assert settings.json()["mode"] == "shadow"

    updated = client.patch(
        "/api/v1/autonomy/settings",
        json={"enabled": True, "mode": "supervised", "auto_approval_threshold": 0.9},
    )
    assert updated.status_code == 200
    assert updated.json()["mode"] == "supervised"
    assert updated.json()["auto_approval_threshold"] == 0.9

    overview_response = client.get("/api/v1/autonomy/overview")
    assert overview_response.status_code == 200
    body = overview_response.json()
    assert body["enabled"] is True
    assert body["mode"] == "supervised"
    assert body["auto_approved"] == 0

    decisions = client.get("/api/v1/autonomy/decisions")
    assert decisions.status_code == 200
    assert isinstance(decisions.json(), list)


def test_autonomy_settings_require_the_admin_role(client, collector_client):
    response = collector_client.patch("/api/v1/autonomy/settings", json={"enabled": True})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_role"


def test_autonomy_evaluation_endpoint_is_available_to_reviewers(
    client, reviewer_client, source
):
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source.id,
            "external_id": "eval-1",
            "original_text": JOB_POST,
        },
    )
    opportunity_id = response.json()["id"]
    evaluation = reviewer_client.post(
        f"/api/v1/autonomy/opportunities/{opportunity_id}/evaluate"
    )
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["classification"] == "freelance_job"
    assert evaluation.json()["decision"] in (
        "would_auto_approve",
        "human_review_required",
    )
