from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_usage_returns_buckets_shape() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/usage", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "interactive" in body and "agent_sdk" in body
    for k in ("today", "ceiling", "remaining_pct"):
        assert k in body["interactive"]
