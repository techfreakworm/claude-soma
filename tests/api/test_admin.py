from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_broadcast_requires_body() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/admin/broadcast", headers=HEADERS, json={})
    assert r.status_code == 422  # missing required field
