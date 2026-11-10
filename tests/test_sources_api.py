def _create(client, **overrides):
    payload = {
        "name": "Source to update",
        "source_type": "manual",
        "source_url": "https://t.me/source_to_update",
        "category": "software",
        "is_active": True,
    }
    payload.update(overrides)
    response = client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_source_update_changes_fields(client):
    source = _create(client)
    response = client.patch(
        f"/api/v1/sources/{source['id']}",
        json={"name": "Renamed source", "category": "design"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed source"
    assert response.json()["category"] == "design"

    read = client.get(f"/api/v1/sources/{source['id']}").json()
    assert read["name"] == "Renamed source"


def test_source_status_uses_json_body(client):
    source = _create(client)
    response = client.patch(
        f"/api/v1/sources/{source['id']}/status", json={"is_active": False}
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    assert (
        client.patch(f"/api/v1/sources/{source['id']}/status", json={}).status_code == 422
    )


def test_empty_source_update_is_rejected(client):
    source = _create(client)
    assert client.patch(f"/api/v1/sources/{source['id']}", json={}).status_code == 422


def test_source_pagination_is_bounded(client):
    assert client.get("/api/v1/sources?limit=201").status_code == 422
    assert client.get("/api/v1/sources?offset=-1").status_code == 422


def test_unknown_source_returns_404(client):
    assert client.get("/api/v1/sources/999999").status_code == 404
    assert client.patch("/api/v1/sources/999999", json={"name": "nope"}).status_code == 404
    assert (
        client.patch("/api/v1/sources/999999/status", json={"is_active": False}).status_code
        == 404
    )
