from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_memory_get_unknown_returns_empty_string() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/memory/does-not-exist", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"project": "does-not-exist", "text": ""}
