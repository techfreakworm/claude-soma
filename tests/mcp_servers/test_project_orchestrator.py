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


def test_register_routine_writes_to_registry() -> None:
    result = orch.register_routine(
        name="weekly-brief",
        kind="cloud",
        schedule="0 9 * * 1",
        target_skill="morning-brief",
        description="Weekly Monday digest",
        created_by="user",
        metadata_json='{"trigger_id": "trg_abc"}',
    )
    assert result == {"registered": "weekly-brief", "kind": "cloud"}
    got = orch._reg().get_routine("weekly-brief")
    assert got is not None
    assert got["name"] == "weekly-brief"
    assert got["kind"] == "cloud"
    assert got["schedule"] == "0 9 * * 1"
    assert got["target_skill"] == "morning-brief"
    assert got["description"] == "Weekly Monday digest"
    assert got["created_by"] == "user"
    assert got["metadata"] == {"trigger_id": "trg_abc"}


def test_register_routine_defaults_optional_fields() -> None:
    result = orch.register_routine(
        name="basic",
        kind="local",
        schedule="*-*-* 12:00:00",
    )
    assert result == {"registered": "basic", "kind": "local"}
    got = orch._reg().get_routine("basic")
    assert got is not None
    assert got["target_skill"] is None
    assert got["description"] is None
    assert got["created_by"] == "bot"
    assert got["metadata"] is None


def test_register_routine_is_upsert() -> None:
    orch.register_routine(
        name="dup", kind="local", schedule="*-*-* 01:00:00",
        description="first",
    )
    orch.register_routine(
        name="dup", kind="local", schedule="*-*-* 02:00:00",
        description="second",
    )
    got = orch._reg().get_routine("dup")
    assert got is not None
    assert got["schedule"] == "*-*-* 02:00:00"
    assert got["description"] == "second"
