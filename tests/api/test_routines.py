from fastapi.testclient import TestClient

from claude_soma.api.main import create_app


HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_list_routines_returns_list() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/routines", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
