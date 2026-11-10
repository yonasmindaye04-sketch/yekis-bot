from types import SimpleNamespace

import pytest

from app.security.auth import load_credentials, validate_security_config

ADMIN_KEY = "admin-test-key-0123456789"
REVIEWER_KEY = "reviewer-test-key-0123456789"


def _seed_opportunity(client, external_id="auth-1"):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Auth source",
            "source_type": "manual",
            "source_url": "https://t.me/auth_source",
            "is_active": True,
        },
    ).json()
    opportunity = client.post(
        "/api/v1/ingestion/manual",
        json={
            "source_id": source["id"],
            "external_id": external_id,
            "original_text": "Need a python developer. Pay $1,000.",
        },
    ).json()
    return source, opportunity


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/v1/sources", None),
        ("POST", "/api/v1/sources", {"name": "source", "source_type": "manual", "source_url": "https://t.me/x"}),
        ("GET", "/api/v1/ingestion/posts", None),
        (
            "POST",
            "/api/v1/ingestion/manual",
            {
                "source_id": 1,
                "external_id": "x",
                "original_text": "Need a python developer. Pay $10.",
            },
        ),
        ("GET", "/api/v1/opportunities", None),
        ("PATCH", "/api/v1/opportunities/1/review", {"decision": "approved"}),
    ],
)
def test_protected_routes_require_credentials(anon_client, method, path, payload):
    response = anon_client.request(method, path, json=payload)
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "authentication_required"


def test_invalid_key_is_rejected(anon_client):
    response = anon_client.get("/api/v1/sources", headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 401


def test_bearer_token_is_accepted(anon_client):
    response = anon_client.get(
        "/api/v1/sources", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    )
    assert response.status_code == 200


def test_admin_can_create_source(client):
    response = client.post(
        "/api/v1/sources",
        json={"name": "Admin source", "source_type": "manual", "source_url": "https://t.me/a"},
    )
    assert response.status_code == 201


def test_reviewer_cannot_create_source(reviewer_client):
    response = reviewer_client.post(
        "/api/v1/sources",
        json={"name": "Reviewer source", "source_type": "manual", "source_url": "https://t.me/a"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_role"


def test_collector_and_analyst_cannot_review(client, collector_client, analyst_client):
    _, opportunity = _seed_opportunity(client)
    payload = {"decision": "approved", "reviewer_note": "should not work"}

    collector = collector_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review", json=payload
    )
    analyst = analyst_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review", json=payload
    )

    assert collector.status_code == 403
    assert analyst.status_code == 403


def test_reviewer_can_review_and_is_recorded(client, reviewer_client):
    _, opportunity = _seed_opportunity(client)
    response = reviewer_client.patch(
        f"/api/v1/opportunities/{opportunity['id']}/review",
        json={"decision": "approved", "reviewer_note": "Verified by reviewer"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_status"] == "approved"
    assert body["reviewed_by"] == "test-reviewer"
    assert body["reviewer_note"] == "Verified by reviewer"


def test_docs_and_openapi_are_disabled_outside_development(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_dashboard_is_served(client):
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "Yekis Intelligence" in response.text
    assert "Review queue" in response.text


def test_security_config_fails_loudly_without_keys_in_production():
    with pytest.raises(RuntimeError, match="AUTH_API_KEYS is empty"):
        validate_security_config(SimpleNamespace(app_env="production", auth_api_keys=""))


def test_load_credentials_rejects_short_keys():
    with pytest.raises(RuntimeError, match="shorter than"):
        load_credentials('{"short": {"role": "admin", "name": "x"}}')


def test_load_credentials_rejects_invalid_role():
    with pytest.raises(RuntimeError, match="invalid role"):
        load_credentials('{"a-very-long-enough-key": {"role": "root", "name": "x"}}')


def test_actor_key_identity_is_stable():
    credentials = load_credentials(
        f'{{"{REVIEWER_KEY}": {{"role": "reviewer", "name": "Ops"}}}}'
    )
    principal = next(iter(credentials.values()))
    assert principal.name == "Ops"
    assert principal.role == "reviewer"
    assert principal.key_id and len(principal.key_id) == 12
