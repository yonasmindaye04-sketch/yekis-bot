from types import SimpleNamespace

from app.config import get_settings
from app.integrations.telegram.publisher import FakePublisher
from app.main import app
from app.services.publishing import publisher_session


def _settings(**overrides):
    values = {
        "publishing_enabled": True,
        "publishing_allowed_channels": ["@audit_publish"],
        "publishing_max_attempts": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _approved_opportunity(client):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Publication source",
            "source_type": "manual",
            "source_url": "https://t.me/publication_source",
            "is_active": True,
        },
    ).json()
    opportunity = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "publication-1",
            "original_text": "Looking for a Python developer. Budget $400.",
        },
    ).json()
    reviewed = client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "approved", "reviewer_note": "looks good"},
    )
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def _override(settings, publisher):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[publisher_session] = lambda: publisher


def _clear_override():
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(publisher_session, None)


def test_publish_endpoint_requires_reviewer_role(client, collector_client):
    opportunity = _approved_opportunity(client)
    fake = FakePublisher()
    _override(_settings(), fake)
    try:
        response = collector_client.post(
            f"/api/v1/opportunities/{opportunity['id']}/publish",
            json={"channel": "@audit_publish"},
        )
    finally:
        _clear_override()
    assert response.status_code == 403
    assert fake.sent == []


def test_publish_endpoint_publishes_and_lists(client):
    opportunity = _approved_opportunity(client)
    fake = FakePublisher()
    _override(_settings(), fake)
    try:
        response = client.post(
            f"/api/v1/opportunities/{opportunity['id']}/publish",
            json={"channel": "@audit_publish"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "published"
        assert body["attempts"] == 1

        listing = client.get("/api/v1/publications")
        assert listing.status_code == 200
        assert len(listing.json()) == 1
    finally:
        _clear_override()

    assert len(fake.sent) == 1
    assert "APPLY ON YEKIS" in fake.sent[0][1]
    assert "https://yekis.com" in fake.sent[0][1]
    assert "Source:" not in fake.sent[0][1]


def test_publish_endpoint_refuses_unapproved_opportunity(client):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Unapproved source",
            "source_type": "manual",
            "source_url": "https://t.me/unapproved_source",
            "is_active": True,
        },
    ).json()
    opportunity = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": "unapproved-1",
            "original_text": "Need a data analyst. Budget $100.",
        },
    ).json()
    fake = FakePublisher()
    _override(_settings(), fake)
    try:
        response = client.post(
            f"/api/v1/opportunities/{opportunity['id']}/publish",
            json={"channel": "@audit_publish"},
        )
    finally:
        _clear_override()
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_approved"
    assert fake.sent == []


def test_publish_endpoint_respects_disabled_flag(client):
    opportunity = _approved_opportunity(client)
    fake = FakePublisher()
    _override(_settings(publishing_enabled=False), fake)
    try:
        response = client.post(
            f"/api/v1/opportunities/{opportunity['id']}/publish",
            json={"channel": "@audit_publish"},
        )
    finally:
        _clear_override()
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "publishing_disabled"
    assert fake.sent == []


def test_publish_endpoint_rejects_non_allowlisted_channel(client):
    opportunity = _approved_opportunity(client)
    fake = FakePublisher()
    _override(_settings(), fake)
    try:
        response = client.post(
            f"/api/v1/opportunities/{opportunity['id']}/publish",
            json={"channel": "@evil_channel"},
        )
    finally:
        _clear_override()
    assert response.status_code == 403
    assert fake.sent == []
