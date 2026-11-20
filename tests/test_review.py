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


def test_field_edit_client_name(client, reviewer_client):
    opportunity = _seed(client, "edit-client-1")
    response = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}",
        json={"client_name": "International Livestock Research Institute (ILRI)"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["client_name"] == "International Livestock Research Institute (ILRI)"

    read = client.get(f"/api/v1/opportunities/{opportunity['id']}").json()
    assert read["client_name"] == "International Livestock Research Institute (ILRI)"


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



def test_closed_job_ingested_is_auto_rejected(client):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Closed source",
            "source_type": "manual",
            "source_url": "https://t.me/closed_source",
            "is_active": True,
        },
    ).json()
    post = (
        "************ Closed ************\n\n"
        "Job Title: Editing Dates/Content and Layout of a Design\n\n"
        "Description: Graphic Design skills needed. Photoshop+Illustrator.\n"
        "Delivery in 48 hours\n\n"
        "#Design\n"
        "@freelance_ethio"
    )
    opp = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "closed-opp-1",
            "original_text": post,
        },
    ).json()

    assert opp["review_status"] == "rejected"
    assert opp["classification"] == "closed_or_filled_job"
    assert opp["approval_method"] == "auto"
    assert "closed, filled, or expired" in opp["approval_reason"]

    # Ensure it never appears in the pending_review queue
    pending = client.get("/api/v1/opportunities?status=pending_review").json()
    assert all(o["id"] != opp["id"] for o in pending)


def test_cleanup_closed_opportunities_and_endpoint(client, reviewer_client, db_session):
    from app.models import Opportunity
    from app.services.review import cleanup_closed_opportunities

    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Test source",
            "source_type": "manual",
            "source_url": "https://t.me/test_cleanup_source",
            "is_active": True,
        },
    ).json()

    # Create an opportunity that was previously in pending_review
    opp_data = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "clean-1",
            "original_text": "Looking for a React developer. Budget $300.",
        },
    ).json()

    # Simulate an opportunity whose text became closed but was left in pending_review
    opp_obj = db_session.get(Opportunity, opp_data["id"])
    opp_obj.post.original_text = (
        "************ Closed ************\n\n"
        "Job Title: Editing Dates/Content and Layout of a Design"
    )
    opp_obj.review_status = "pending_review"
    db_session.commit()

    # Test Python cleanup function
    cleaned = cleanup_closed_opportunities(db_session)
    assert opp_data["id"] in cleaned

    db_session.refresh(opp_obj)
    assert opp_obj.review_status == "rejected"
    assert opp_obj.approval_method == "auto"
    assert opp_obj.classification == "closed_or_filled_job"

    # Put another one in needs_review with closed marker and test API endpoint
    opp_obj.review_status = "needs_review"
    opp_obj.title = "[Closed] Senior Python Developer"
    db_session.commit()

    res = reviewer_client.post("/api/v1/opportunities/cleanup-closed")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["cleaned_count"] >= 1
    assert opp_data["id"] in data["cleaned_ids"]

    db_session.refresh(opp_obj)
    assert opp_obj.review_status == "rejected"


def test_sorting_and_actionable_queue(client, reviewer_client, db_session):
    from app.models import Publication

    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Queue Source",
            "source_type": "manual",
            "source_url": "https://t.me/queue_source",
            "is_active": True,
        },
    ).json()

    opp1 = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "queue-1",
            "original_text": "Need Python engineer for backend API development. Budget $1000.",
        },
    ).json()

    opp2 = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "queue-2",
            "original_text": "Need UI Designer for mobile app wireframing. Budget $600.",
        },
    ).json()

    # 1. Default sorting is newest first (id descending)
    res_default = client.get("/api/v1/opportunities").json()
    ids_default = [item["id"] for item in res_default]
    assert ids_default.index(opp2["id"]) < ids_default.index(opp1["id"])

    # 2. Test explicit sort parameter
    res_newest = client.get("/api/v1/opportunities?sort=newest").json()
    assert [item["id"] for item in res_newest][:2] == [opp2["id"], opp1["id"]]

    # 3. Simulate opp1 being published
    pub = Publication(
        opportunity_id=opp1["id"],
        channel="test_channel",
        status="published",
        created_by="system",
    )
    db_session.add(pub)
    db_session.commit()

    # 4. Actionable queue (default) excludes published and rejected
    res_actionable = client.get("/api/v1/opportunities?status=actionable").json()
    actionable_ids = [item["id"] for item in res_actionable]
    assert opp1["id"] not in actionable_ids
    assert opp2["id"] in actionable_ids

    # 5. Verify is_published flag is returned
    opp1_read = client.get(f"/api/v1/opportunities/{opp1['id']}").json()
    assert opp1_read["is_published"] is True
    opp2_read = client.get(f"/api/v1/opportunities/{opp2['id']}").json()
    assert opp2_read["is_published"] is False
