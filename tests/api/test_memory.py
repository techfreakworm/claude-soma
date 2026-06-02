from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_memory_get_unknown_returns_empty_string() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/memory/does-not-exist", headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["project"] == "does-not-exist"
    assert data["text"] == ""
    stats = data["stats"]
    assert stats["bytes"] == 0
    assert stats["lines"] == 0
    assert stats["chars"] == 0
    assert stats["sections"] == 0
    assert stats["headings"] == 0
    assert stats["last_modified"] == 0.0
    assert stats["path"] == ""
