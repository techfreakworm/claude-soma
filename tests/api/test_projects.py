from fastapi.testclient import TestClient

from claude_soma.api.main import create_app


HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_list_projects_requires_auth() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 403


def test_list_projects_returns_list_when_authed() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_project_detail_404_for_unknown() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects/does-not-exist", headers=HEADERS)
    assert r.status_code == 404
