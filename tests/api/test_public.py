from fastapi.testclient import TestClient

from claude_soma.api.main import create_app


def test_public_stats_returns_anonymized_shape() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/public/stats")
    assert r.status_code == 200
    body = r.json()
    for k in ("messages_today", "active_projects",
              "decisions_today", "uptime_hours"):
        assert k in body
        assert isinstance(body[k], (int, float))
