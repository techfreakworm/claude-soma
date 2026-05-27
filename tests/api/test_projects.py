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


def test_project_team_404_for_unknown() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects/does-not-exist/team", headers=HEADERS)
    assert r.status_code == 404


def test_project_team_returns_roster(tmp_path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    from claude_soma.mcp_servers.project_orchestrator import server as orch

    # Create + populate the registry SINGLETON on THIS (test) thread, exactly as
    # the live orchestrator does, then hit the endpoint -- whose sync handler runs
    # in a Starlette threadpool worker. This reproduces the cross-thread access
    # that 500'd in production; with the thread-safe Registry it returns 200.
    orch._reset_singletons_for_tests()
    orch._reg().register("tm", agent_id="soma-proj-tm", type_="custom",
                         cwd="/x", rc_url=None)
    roster = [{"handle": "teammate-1", "role": "writer", "status": "active"}]
    monkeypatch.setattr(orch, "discover_team", lambda agent_id: roster)

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects/tm/team", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"name": "tm", "team": roster}
    orch._reset_singletons_for_tests()  # don't leak this test's singleton
