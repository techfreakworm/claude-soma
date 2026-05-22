from fastapi.testclient import TestClient

from claude_soma.api.main import create_app


def test_healthz_returns_200_with_status_ok() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
