# tests/mcp_servers/test_project_orchestrator.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator import server as orch


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ORCH_DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setenv("HERMES_PROJECTS_ROOT", str(tmp_path / "projects"))
    orch._reset_singletons_for_tests()


def _ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = ""
    m.returncode = 0
    return m


def test_spawn_project_registers_and_returns_url() -> None:
    pane = "remote: https://rc.claude.com/alpha-rc\n"
    with patch("subprocess.run", side_effect=[_ok(), _ok(pane)]):
        r = orch.spawn_project_impl(
            name="alpha", type_="web-scraper",
            brief="Build a scraper.", permission_mode="acceptEdits",
        )
    assert r["agent_id"] == "soma-proj-alpha"
    assert r["rc_url"] == "https://rc.claude.com/alpha-rc"
    listed = orch.list_projects_impl()
    assert any(p["name"] == "alpha" for p in listed)


def test_kill_project_marks_killed_and_terminates_tmux() -> None:
    # subprocess.run call sequence: 2 for spawn (tmux new-session + capture-pane),
    # 1 for kill (tmux kill-session).
    with patch("subprocess.run", side_effect=[_ok(), _ok(""), _ok("")]) as run_mock:
        orch.spawn_project_impl(name="beta", type_="custom",
                                brief="Test.", permission_mode="default")
        r = orch.kill_project_impl(name="beta", archive=True)
    assert r["killed_at"] is not None
    assert all(p["name"] != "beta" for p in orch.list_projects_impl())
    # Last subprocess.run was the kill_session: tmux kill-session -t soma-proj-beta
    last_cmd = run_mock.call_args_list[-1].args[0]
    assert "kill-session" in last_cmd
    assert "soma-proj-beta" in last_cmd


def test_get_status_returns_idle_for() -> None:
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]):
        orch.spawn_project_impl(name="gamma", type_="custom",
                                brief="x", permission_mode="default")
    s = orch.get_status_impl("gamma")
    assert s["name"] == "gamma"
    assert "idle_for_seconds" in s


def test_spawn_unknown_type_falls_back_to_custom() -> None:
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]):
        r = orch.spawn_project_impl(name="d", type_="not-a-type",
                                    brief="x", permission_mode="default")
    assert r["agent_id"] == "soma-proj-d"
    p = orch.get_status_impl("d")
    assert p["type"] in {"custom", "not-a-type"}
