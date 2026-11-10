from sqlalchemy import func, select

from app.models import ReviewEvent


def _seed(client, external_id="review-1"):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Review source",
            "source_type": "manual",
            "source_url": "https://t.me/review_source",
            "is_active": True,
        },
    ).json()
    return client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": external_id,
            "original_text": "Looking for a React developer. Budget $300. Delivery in 2 weeks.",
        },
    ).json()


def test_review_note_round_trip_and_event(client, reviewer_client, db_session):
    opportunity = _seed(client)
    response = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "rejected", "reviewer_note": "No verifiable source"},
    )
    assert response.status_code == 200, response.text
    reviewed = response.json()
    assert reviewed["reviewer_note"] == "No verifiable source"
    assert reviewed["reviewed_by"] == "test-reviewer"

    read = client.get(f"/api/v1/opportunities/{opportunity['id']}").json()
    assert read["reviewer_note"] == "No verifiable source"

    events = client.get(f"/api/v1/opportunities/{opportunity['id']}/events").json()
    assert len(events) == 1
    assert events[0]["actor"] == "test-reviewer"
    assert events[0]["from_status"] == "pending_review"
    assert events[0]["to_status"] == "rejected"
    assert events[0]["note"] == "No verifiable source"
    assert db_session.scalar(select(func.count()).select_from(ReviewEvent)) == 1


def test_repeating_the_same_decision_is_idempotent(client, reviewer_client):
    opportunity = _seed(client)
    first = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "approved"},
    )
    second = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "approved"},
    )
    assert first.status_code == second.status_code == 200
    events = client.get(f"/api/v1/opportunities/{opportunity['id']}/events").json()
    assert len(events) == 1


def test_illegal_transition_returns_409(client, reviewer_client):
    opportunity = _seed(client)
    reject = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "rejected"},
    )
    assert reject.status_code == 200

    invalid = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "approved"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "invalid_transition"
    events = client.get(f"/api/v1/opportunities/{opportunity['id']}/events").json()
    assert len(events) == 1


def test_field_edit_is_recorded_with_before_and_after(client, reviewer_client):
    opportunity = _seed(client)
    response = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}",
        json={"title": "Edited title", "budget_amount": 450.0, "budget_currency": "USD"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Edited title"

    events = client.get(f"/api/v1/opportunities/{opportunity['id']}/events").json()
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "field_edit"
    assert event["actor"] == "test-reviewer"
    assert event["changes"]["title"]["to"] == "Edited title"
    assert event["changes"]["budget_amount"]["to"] == 450.0


def test_field_edit_with_no_changes_does_not_create_an_event(client, reviewer_client):
    opportunity = _seed(client)
    response = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}",
        json={"title": opportunity["title"]},
    )
    assert response.status_code == 200
    events = client.get(f"/api/v1/opportunities/{opportunity['id']}/events").json()
    assert events == []


def test_unknown_opportunity_returns_404(client):
    assert client.get("/api/v1/opportunities/999999").status_code == 404
    assert client.get("/api/v1/opportunities/999999/events").status_code == 404


def test_empty_edit_payload_is_rejected(client):
    opportunity = _seed(client)
    response = client.patch(f"/api/v1/opportunities/{opportunity['id']}", json={})
    assert response.status_code == 422


def test_opportunity_list_filters_by_status(client):
    _seed(client, "risk-1")

    assert len(client.get("/api/v1/opportunities?status=pending_review").json()) == 1
    assert client.get("/api/v1/opportunities?status=approved").json() == []
    assert client.get("/api/v1/opportunities?limit=201").status_code == 422
