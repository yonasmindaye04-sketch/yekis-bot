from sqlalchemy import func, select

from app.models import CollectedPost, Opportunity


def _create_source(client, **overrides):
    payload = {
        "name": "Yekis audit source",
        "source_type": "manual",
        "source_url": "https://t.me/audit_test_channel",
        "category": "software",
        "is_active": True,
    }
    payload.update(overrides)
    response = client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_ingestion_creates_one_post_and_one_opportunity(client):
    source = _create_source(client)
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "demo-001",
            "original_text": (
                "Looking for a React developer to build a dashboard. "
                "Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks."
            ),
            "original_url": "https://t.me/audit_test_channel/1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["opportunity_type"] == "freelance_job"
    assert body["budget_amount"] == 300
    assert body["review_status"] == "pending_review"
    assert body["risk_flags"] == []

    posts = client.get("/api/v1/ingestion/posts").json()
    assert len(posts) == 1
    assert posts[0]["content_hash"] == posts[0]["content_hash"]
    assert len(posts[0]["content_hash"]) == 64


def test_duplicate_ingestion_returns_409_with_stable_code(client):
    source = _create_source(client)
    payload = {
        "source_id": source["id"],
        "external_id": "demo-001",
        "original_text": "Need a python developer. Pay $1,000.",
    }
    assert client.post("/api/v1/ingestion/manual", json=payload).status_code == 201
    duplicate = client.post("/api/v1/ingestion/manual", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_post"

    # The failed request must not poison the request-scoped session.
    assert client.get("/api/v1/ingestion/posts").status_code == 200


def test_risky_text_is_flagged_and_routed_to_review(client, db_session):
    source = _create_source(client)
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "scam-001",
            "original_text": (
                "URGENT! Send 500 birr registration fee via telebirr to get a $5000 "
                "remote job. Pay upfront."
            ),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_status"] == "needs_review"
    assert "upfront_payment" in body["risk_flags"]
    assert body["budget_amount"] is None

    # Original text is preserved verbatim; flagging is not a verdict.
    post = db_session.scalar(select(CollectedPost))
    assert "registration fee" in post.original_text
    assert db_session.scalar(select(func.count()).select_from(Opportunity)) == 1


def test_inactive_source_returns_409(client):
    source = _create_source(client, is_active=False)
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "inactive-1",
            "original_text": "Need a data analyst. Budget $400.",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_inactive"


def test_missing_source_returns_404(client):
    response = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": 99999,
            "external_id": "missing-1",
            "original_text": "Need a data analyst. Budget $400.",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "source_not_found"


def test_posts_pagination_is_bounded(client):
    assert client.get("/api/v1/ingestion/posts", params={"limit": 201}).status_code == 422
    assert client.get("/api/v1/ingestion/posts", params={"limit": 0}).status_code == 422
