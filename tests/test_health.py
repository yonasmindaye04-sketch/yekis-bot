import app.routers.health as health_router


class _BrokenEngine:
    def connect(self):
        raise RuntimeError("database is down")


def test_health_returns_200_when_database_is_reachable(anon_client):
    response = anon_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_health_returns_503_when_database_is_down(anon_client, monkeypatch):
    monkeypatch.setattr(health_router, "engine", _BrokenEngine())
    response = anon_client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert "down" not in response.text
