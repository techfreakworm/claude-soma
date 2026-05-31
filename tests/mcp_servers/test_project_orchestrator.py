# tests/mcp_servers/test_project_orchestrator.py
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator import server as orch
from claude_soma.mcp_servers.project_orchestrator import spawner
from claude_soma.mcp_servers.project_orchestrator.spawner import resume_background_lead


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


def test_touch_project_bumps_last_activity(monkeypatch) -> None:
    """touch_project must update last_activity in the registry and return
    touched_at. The bot talks to leads via raw tmux send-keys (bypassing
    send_to_project_impl's automatic touch), so this tool is the only way the
    idle clock advances for those conversations."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="touch-me", type_="custom",
                                brief="x", permission_mode="default")
    before = time.time()
    result = orch.touch_project_impl(name="touch-me")
    after = time.time()
    assert result["name"] == "touch-me"
    assert before <= result["touched_at"] <= after
    row = orch._reg().get("touch-me")
    assert row is not None
    assert float(row["last_activity"]) >= before


def test_touch_project_raises_for_unknown_name() -> None:
    with pytest.raises(RuntimeError, match="no project named"):
        orch.touch_project_impl(name="no-such-lead")


def test_cap_intersect_skips_dead_leads(monkeypatch) -> None:
    """_reconcile_active intersects active registry rows with is_lead_alive before
    counting toward HERMES_MAX_CONCURRENT_PROJECTS. Stale 'active' rows for dead
    leads must not block new spawns.

    Seed 6 active rows, mock is_lead_alive to return False for 2 of them; a 7th
    spawn must succeed because the effective count is 4 (< cap=6)."""
    _no_url_poll(monkeypatch)
    monkeypatch.setattr(orch, "MAX_CONCURRENT", 6)

    # Spawn 6 leads -- all active in the registry. Subprocess is fully mocked.
    names = [f"cap-lead-{i}" for i in range(6)]
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        for n in names:
            orch.spawn_project_impl(name=n, type_="custom",
                                    brief="x", permission_mode="default")

    # Effective count = 4: leads 0 and 1 are dead, 2-5 are alive.
    dead = {"cap-lead-0", "cap-lead-1"}
    def _liveness(name: str) -> bool:
        return name not in dead

    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        with patch.object(orch, "is_lead_alive", side_effect=_liveness):
            r = orch.spawn_project_impl(name="cap-lead-6", type_="custom",
                                        brief="x", permission_mode="default")

    assert r["agent_id"] == "soma-proj-cap-lead-6"
    # Dead leads must have been flipped to 'dead' in the registry.
    for n in dead:
        assert orch._reg().get(n)["status"] == "dead"


def test_spawn_project_prepends_notify_convention(monkeypatch) -> None:
    """spawn_project_impl must prepend the Standing Notify Convention block to
    the brief that is passed to spawn_background_lead so every lead receives
    the emission instructions on first spawn (and on --continue restarts via
    transcript replay)."""
    _no_url_poll(monkeypatch)
    captured: dict = {}

    original_spawn = orch.spawn_background_lead

    def _capture_spawn(**kwargs):
        captured["brief"] = kwargs.get("brief", "")
        return original_spawn(**kwargs)

    with patch.object(orch, "spawn_background_lead", side_effect=_capture_spawn):
        with patch("subprocess.run", side_effect=_tmux_side_effect("")):
            orch.spawn_project_impl(
                name="notify-test", type_="custom",
                brief="Do the thing.", permission_mode="default",
            )

    assert "brief" in captured
    assert "Standing Notify Convention" in captured["brief"]
    assert "STARTED" in captured["brief"]
    assert "MILESTONE" in captured["brief"]
    assert "COMPLETED" in captured["brief"]
    assert "NEEDS_INPUT" in captured["brief"]
    assert "ERROR" in captured["brief"]
    assert "mcp__hermes-notify__notify_orchestrator" in captured["brief"]
    assert captured["brief"].index("Standing Notify Convention") < captured["brief"].index("Do the thing.")


def test_spawn_project_persists_session_uuid(monkeypatch) -> None:
    """spawn_project_impl must call set_session_uuid after a successful spawn
    so the registry holds the UUID for future --resume operations."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        r = orch.spawn_project_impl(
            name="uuid-persist", type_="custom",
            brief="Test session uuid.", permission_mode="default",
        )
    # Return value must include session_uuid.
    assert "session_uuid" in r
    assert r["session_uuid"] is not None
    # Registry must have it persisted.
    stored = orch._reg().get_session_uuid("uuid-persist")
    assert stored == r["session_uuid"]


def test_resume_project_raises_if_no_session_uuid(monkeypatch) -> None:
    """resume_project_impl raises if the project has no session_uuid (was spawned
    before session tracking). Operator must kill and re-spawn."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="nosid-proj", type_="custom",
                                brief="x", permission_mode="default")
    # Manually clear the session_uuid to simulate pre-tracking spawn.
    orch._reg().set_session_uuid("nosid-proj", None)  # type: ignore[arg-type]

    # Patch is_lead_alive to False so we don't fail on the alive check.
    with patch.object(orch, "is_lead_alive", return_value=False):
        with pytest.raises(RuntimeError, match="no session_uuid"):
            orch.resume_project_impl(name="nosid-proj")


def test_resume_project_raises_if_lead_alive(monkeypatch) -> None:
    """resume_project_impl raises if the lead is still alive to prevent duplicates."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="alive-proj", type_="custom",
                                brief="x", permission_mode="default")
    with patch.object(orch, "is_lead_alive", return_value=True):
        with pytest.raises(RuntimeError, match="still alive"):
            orch.resume_project_impl(name="alive-proj")


def test_resume_project_uses_resume_flag(monkeypatch) -> None:
    """resume_project_impl spawns via resume_background_lead with --resume <uuid>."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        spawn_r = orch.spawn_project_impl(name="resume-proj", type_="custom",
                                          brief="x", permission_mode="default")
    original_uuid = spawn_r["session_uuid"]
    assert original_uuid is not None

    captured: dict = {}

    def _fake_resume(**kwargs):
        captured.update(kwargs)
        return {"agent_id": "soma-proj-resume-proj", "rc_url": "", "cwd": "/x",
                "session_uuid": kwargs["session_uuid"]}

    with patch.object(orch, "is_lead_alive", return_value=False):
        with patch.object(orch, "resume_background_lead", side_effect=_fake_resume):
            r = orch.resume_project_impl(name="resume-proj")

    assert captured["session_uuid"] == original_uuid
    assert r["session_uuid"] == original_uuid
    # Registry updated: status active, uuid unchanged.
    assert orch._reg().get_session_uuid("resume-proj") == original_uuid


def test_resume_project_on_reaper_killed_lead(monkeypatch) -> None:
    """resume_project_impl must work on a lead that the reaper hibernated
    (status='killed', session_uuid preserved). resume_project_impl has no status
    gate — it only requires the row to exist and session_uuid to be non-null."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        spawn_r = orch.spawn_project_impl(name="reaped-proj", type_="custom",
                                          brief="x", permission_mode="default")
    original_uuid = spawn_r["session_uuid"]
    assert original_uuid is not None

    # Simulate reaper hibernation: flip status to 'killed', session_uuid unchanged.
    orch._reg().set_status("reaped-proj", "killed", bump_activity=False)
    assert orch._reg().get_session_uuid("reaped-proj") == original_uuid

    captured: dict = {}

    def _fake_resume(**kwargs):
        captured.update(kwargs)
        return {
            "agent_id": "soma-proj-reaped-proj",
            "rc_url": "",
            "cwd": "/x",
            "session_uuid": kwargs["session_uuid"],
        }

    with patch.object(orch, "is_lead_alive", return_value=False):
        with patch.object(orch, "resume_background_lead", side_effect=_fake_resume):
            r = orch.resume_project_impl(name="reaped-proj")

    assert captured["session_uuid"] == original_uuid
    assert r["session_uuid"] == original_uuid
    # Registry must retain the same session_uuid after resume.
    assert orch._reg().get_session_uuid("reaped-proj") == original_uuid


def test_get_team_impl_persists_members_to_registry(monkeypatch) -> None:
    """get_team_impl must upsert discovered teammates into registry.team_members
    so a later resume can re-establish the team."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="persist-team", type_="custom",
                                brief="x", permission_mode="default")
    roster = [
        {"handle": "teammate-1", "role": "PM", "status": "active"},
        {"handle": "teammate-2", "role": "dev", "status": "active"},
    ]
    with patch.object(orch, "discover_team", return_value=roster):
        orch.get_team_impl("persist-team")
    members = orch._reg().get_team_members("persist-team")
    assert {m["teammate_handle"] for m in members} == {"teammate-1", "teammate-2"}
    pm = next(m for m in members if m["teammate_handle"] == "teammate-1")
    assert pm["role"] == "PM"
    assert pm["brief"] == "PM"


def test_resume_project_injects_team_context_into_prompt(monkeypatch) -> None:
    """When persisted team members exist, resume_project_impl passes a
    resume_prompt_suffix to resume_background_lead containing the roster."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="rteam", type_="custom",
                                brief="x", permission_mode="default")
    orch._reg().upsert_team_member("rteam", "teammate-1", "PM", "PM")

    captured: dict = {}

    def _fake_resume(**kwargs):
        captured.update(kwargs)
        return {"agent_id": "soma-proj-rteam", "rc_url": "", "cwd": "/x",
                "session_uuid": kwargs["session_uuid"]}

    with patch.object(orch, "is_lead_alive", return_value=False):
        with patch.object(orch, "resume_background_lead", side_effect=_fake_resume):
            orch.resume_project_impl(name="rteam")

    suffix = captured.get("resume_prompt_suffix")
    assert suffix is not None
    assert "teammate-1" in suffix
    assert "PM" in suffix
    assert "re-establish your team" in suffix


def test_resume_project_no_suffix_when_no_team(monkeypatch) -> None:
    """When no team members are persisted, resume_prompt_suffix is None
    so the fixed S14 prompt is not modified."""
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="rno-team", type_="custom",
                                brief="x", permission_mode="default")

    captured: dict = {}

    def _fake_resume(**kwargs):
        captured.update(kwargs)
        return {"agent_id": "soma-proj-rno-team", "rc_url": "", "cwd": "/x",
                "session_uuid": kwargs["session_uuid"]}

    with patch.object(orch, "is_lead_alive", return_value=False):
        with patch.object(orch, "resume_background_lead", side_effect=_fake_resume):
            orch.resume_project_impl(name="rno-team")

    assert captured.get("resume_prompt_suffix") is None


def test_kill_project_archive_logs_when_no_memory(monkeypatch, caplog) -> None:
    """When archive=True and the lead's cwd has no .claude/ memory dir, a
    warning must be logged so callers can distinguish 'archived' from 'skipped'
    rather than silently getting a None return value."""
    import logging
    _no_url_poll(monkeypatch)
    with patch("subprocess.run", side_effect=_tmux_side_effect("")):
        orch.spawn_project_impl(name="no-mem", type_="custom",
                                brief="x", permission_mode="default")

    p = orch._reg().get("no-mem")
    assert p is not None
    memory_dir = Path(p["cwd"]) / ".claude"
    assert not memory_dir.exists(), "test requires no .claude/ dir in cwd"

    with caplog.at_level(logging.WARNING, logger="claude_soma.mcp_servers.project_orchestrator.server"):
        with patch.object(orch, "kill_session"):
            with patch.object(orch, "is_lead_alive", return_value=False):
                result = orch.kill_project_impl(name="no-mem", archive=True)

    assert result["name"] == "no-mem"
    assert any("nothing to archive" in record.message and "no-mem" in record.message
               for record in caplog.records)
