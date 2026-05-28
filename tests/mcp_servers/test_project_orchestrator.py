# tests/mcp_servers/test_project_orchestrator.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator import server as orch
from claude_soma.mcp_servers.project_orchestrator import spawner


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ORCH_DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setenv("HERMES_PROJECTS_ROOT", str(tmp_path / "projects"))
    # spawn creates the per-lead log dir for real; keep it off /var/log.
    monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(tmp_path / "leadlogs"))
    # Lead MCP config absent by default so --mcp-config is omitted deterministically.
    monkeypatch.setenv("HERMES_LEAD_MCP_CONFIG", str(tmp_path / "absent-lead-mcp.json"))
    orch._reset_singletons_for_tests()


def _no_url_poll(monkeypatch) -> None:
    """Short-circuit the spawner's rc.claude.com poll for tests that feed an
    empty pane. _capture_rc_url loops calling `tmux capture-pane` for up to
    RC_URL_POLL_SECONDS (default 30s, sleeping RC_URL_POLL_INTERVAL between
    polls). Zeroing the timeout makes it return "" immediately with zero
    capture-pane calls instead of hanging. The constant is read at call time
    (see _capture_rc_url's docstring), so patching the module attribute works.

    Used only by the no-URL tests; test_spawn_project_registers_and_returns_url
    keeps the real poll so its first capture finds the URL it supplies.
    """
    monkeypatch.setattr(spawner, "RC_URL_POLL_SECONDS", 0)


def _ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = ""
    m.returncode = 0
    return m


def _tmux_side_effect(stdout: str = ""):
    """Return a subprocess.run side_effect that's independent of call count.

    The spawn path issues one `tmux new-session` then polls `tmux
    capture-pane`; kill issues `tmux kill-session`. Inspect the command (first
    positional arg, a list) so every call gets a valid result no matter how
    many polls the spawner makes — this keeps the tests from regressing if the
    spawner's call pattern changes again. capture-pane returns `stdout` (the
    simulated pane contents); everything else returns a generic success.
    """

    def _run(*args, **kwargs) -> MagicMock:
        cmd = args[0] if args else kwargs.get("args", [])
        if "capture-pane" in cmd:
            return _ok(stdout)
        return _ok()

    return _run


def test_spawn_project_registers_and_returns_url() -> None:
    pane = "remote: https://rc.claude.com/alpha-rc\n"
    with patch("subprocess.run", side_effect=[_ok(), _ok(pane)]):
        r = orch.spawn_project_impl(
            name="alpha", type_="web-scraper",
            brief="Build a scraper.", permission_mode="acceptEdits",
        )
    assert r["agent_id"] == "soma-proj-alpha"
    assert r["rc_url"] == "https://rc.claude.com/alpha-rc"
    # list_projects now reconciles liveness against the lead's tmux session;
    # the mocked spawn has no real session, so pretend it's alive here.
    with patch.object(orch, "is_lead_alive", return_value=True):
        listed = orch.list_projects_impl()
    assert any(p["name"] == "alpha" for p in listed)


def test_kill_project_marks_killed_and_terminates_tmux(monkeypatch) -> None:
    # side_effect is a callable so the test is independent of how many
    # capture-pane polls spawn does (here, zero, since RC_URL_POLL_SECONDS=0).
    # kill_session runs after spawn, so the LAST subprocess call is still tmux
    # kill-session. is_lead_alive is patched to False so the post-kill
    # verification confirms the lead is gone and the happy path completes.
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")) as run_mock:
        with patch.object(orch, "is_lead_alive", return_value=False):
            orch.spawn_project_impl(name="beta", type_="custom",
                                    brief="Test.", permission_mode="default")
            r = orch.kill_project_impl(name="beta", archive=True)
    assert r["killed_at"] is not None
    assert all(p["name"] != "beta" for p in orch.list_projects_impl())
    # Last subprocess.run was the kill_session: tmux kill-session -t soma-proj-beta
    last_cmd = run_mock.call_args_list[-1].args[0]
    assert "kill-session" in last_cmd
    assert "soma-proj-beta" in last_cmd


def test_kill_project_raises_if_lead_survives_kill(monkeypatch) -> None:
    """kill_project_impl must raise RuntimeError and NOT flip the registry to
    'killed' when is_lead_alive returns True after both kill attempts."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="zombie-kill", type_="custom",
                                brief="x", permission_mode="default")
    with patch.object(orch, "kill_session") as mock_kill:
        with patch.object(orch, "is_lead_alive", return_value=True):
            with pytest.raises(RuntimeError, match="still alive"):
                orch.kill_project_impl(name="zombie-kill", archive=True)
    # kill_session must have been called twice (initial attempt + one retry).
    assert mock_kill.call_count == 2
    # Registry must NOT have been flipped to "killed".
    assert orch._reg().get("zombie-kill")["status"] == "active"


def test_get_status_returns_idle_for(monkeypatch) -> None:
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="gamma", type_="custom",
                                brief="x", permission_mode="default")
    with patch.object(orch, "is_lead_alive", return_value=True):
        s = orch.get_status_impl("gamma")
    assert s["name"] == "gamma"
    assert "idle_for_seconds" in s


def test_spawn_unknown_type_falls_back_to_custom(monkeypatch) -> None:
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        r = orch.spawn_project_impl(name="d", type_="not-a-type",
                                    brief="x", permission_mode="default")
    assert r["agent_id"] == "soma-proj-d"
    with patch.object(orch, "is_lead_alive", return_value=True):
        p = orch.get_status_impl("d")
    assert p["type"] in {"custom", "not-a-type"}


def test_list_projects_reconciles_dead_lead(monkeypatch) -> None:
    """A lead whose tmux session vanished must drop out of list_projects and
    have its registry status flipped to 'dead' (the bug: it stayed 'active')."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="zombie", type_="custom",
                                brief="x", permission_mode="default")
    with patch.object(orch, "is_lead_alive", return_value=False):
        listed = orch.list_projects_impl()
    assert all(p["name"] != "zombie" for p in listed)
    assert orch._reg().get("zombie")["status"] == "dead"


def test_get_status_reports_dead_when_session_gone(monkeypatch) -> None:
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="ghost", type_="custom",
                                brief="x", permission_mode="default")
    with patch.object(orch, "is_lead_alive", return_value=False):
        s = orch.get_status_impl("ghost")
    assert s["status"] == "dead"
    assert orch._reg().get("ghost")["status"] == "dead"


def test_get_status_keeps_active_when_alive(monkeypatch) -> None:
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="healthy", type_="custom",
                                brief="x", permission_mode="default")
    with patch.object(orch, "is_lead_alive", return_value=True):
        s = orch.get_status_impl("healthy")
    assert s["status"] == "active"


def test_dead_lead_frees_a_concurrency_slot(monkeypatch) -> None:
    """A ghost lead (dead but still 'active' in the registry) must not count
    against MAX_CONCURRENT -- reconciliation frees the slot at spawn time."""
    _no_url_poll(monkeypatch)
    monkeypatch.setattr(orch, "MAX_CONCURRENT", 1)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="first", type_="custom",
                                brief="x", permission_mode="default")
        # Cap is 1 and 'first' is active; a second spawn would be refused if
        # 'first' still counted. Its session has vanished, so the slot frees.
        with patch.object(orch, "is_lead_alive", return_value=False):
            r = orch.spawn_project_impl(name="second", type_="custom",
                                        brief="x", permission_mode="default")
    assert r["agent_id"] == "soma-proj-second"
    assert orch._reg().get("first")["status"] == "dead"


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


def test_get_team_impl_returns_roster(monkeypatch) -> None:
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="tm", type_="custom",
                                brief="x", permission_mode="default")
    roster = [{"handle": "teammate-1", "role": "writer", "status": "active"}]
    with patch.object(orch, "discover_team", return_value=roster) as dt:
        t = orch.get_team_impl("tm")
    # discover_team is called with the lead's agent_id (soma-proj-<name>).
    assert dt.call_args_list[0].args[0] == "soma-proj-tm"
    assert t == {"name": "tm", "team": roster}


def test_get_team_impl_unknown_project_raises() -> None:
    with pytest.raises(RuntimeError):
        orch.get_team_impl("does-not-exist")
