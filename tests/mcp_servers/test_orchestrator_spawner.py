# tests/mcp_servers/test_orchestrator_spawner.py
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator import spawner
from claude_soma.mcp_servers.project_orchestrator.spawner import (
    spawn_background_lead, BriefTooLong, InvalidProjectName, kill_session
)


@pytest.fixture(autouse=True)
def _isolate_spawner(tmp_path: Path, monkeypatch) -> None:
    # Pre-trust state lives in ~/.claude.json on the real machine. Point the
    # spawner at a tmp file so tests don't bulldoze the dev user's actual file.
    monkeypatch.setenv(
        "HERMES_CLAUDE_GLOBAL_JSON", str(tmp_path / "claude.json"),
    )
    # _capture_rc_url polls every RC_URL_POLL_INTERVAL seconds up to
    # RC_URL_POLL_SECONDS. With non-zero values the "no URL in mock output"
    # tests would actually wait. Set both to 0 so the loop runs once and exits.
    monkeypatch.setattr(spawner, "RC_URL_POLL_SECONDS", 0)
    monkeypatch.setattr(spawner, "RC_URL_POLL_INTERVAL", 0)
    # spawn creates the per-lead log dir for real (pathlib, not subprocess), so
    # point it at tmp_path instead of the real /var/log/claude-soma.
    monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(tmp_path / "leadlogs"))


def _ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = ""
    m.returncode = 0
    return m


def test_spawn_calls_claude_bg_with_expected_args(tmp_path: Path) -> None:
    cwd = tmp_path / "my-project"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("hello world\n")]) as run:
        result = spawn_background_lead(
            name="my-project", brief="Build it.", cwd=cwd,
            permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    # The new-session call is now wrapped in `sudo systemd-run` so the lead
    # lands in its own cgroup; tmux appears after the `--` separator.
    assert args[0].endswith("sudo")
    assert any(a.endswith("systemd-run") for a in args)
    assert "--unit=claude-soma-lead-my-project.service" in args
    assert "--" in args
    assert any(a.endswith("tmux") for a in args)
    # Dedicated tmux socket so the server is the only thing on it.
    assert "-L" in args
    sock_idx = args.index("-L") + 1
    assert args[sock_idx] == "soma-lead-my-project"
    assert "new-session" in args
    assert "-d" in args
    assert "-s" in args
    sess_idx = args.index("-s") + 1
    assert "my-project" in args[sess_idx]
    assert "-c" in args
    cwd_idx = args.index("-c") + 1
    assert args[cwd_idx] == str(cwd)
    assert any("claude" in a for a in args)
    assert "--add-dir" in args and str(cwd) in args
    assert "--permission-mode" in args and "acceptEdits" in args
    # The brief is the final argument to new-session -- now right before the
    # `;` that starts the chained pipe-pane logging command.
    sep_idx = args.index(";")
    assert args[sep_idx - 1] == "Build it."
    assert "--bg" not in args
    assert "--output-format" not in args
    assert result["agent_id"].endswith("my-project")
    assert result["cwd"] == str(cwd)
    assert isinstance(result["rc_url"], str)


def test_spawn_rejects_long_brief(tmp_path: Path) -> None:
    with pytest.raises(BriefTooLong):
        spawn_background_lead(
            name="big", brief="x" * 200_000, cwd=tmp_path,
            permission_mode="acceptEdits",
        )


def test_spawn_rejects_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(InvalidProjectName):
        spawn_background_lead(
            name="Bad Name!", brief="ok", cwd=tmp_path,
            permission_mode="acceptEdits",
        )


def test_spawn_uses_tmux_with_native_claude_binary(tmp_path: Path) -> None:
    cwd = tmp_path / "alpha"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="alpha", brief="do work", cwd=cwd,
            permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert any(a.endswith("tmux") for a in args), args
    assert any(a.endswith("claude") or a == "claude" for a in args), args
    assert "--bg" not in args, args
    assert "--output-format" not in args, args


def test_spawn_scrapes_rc_url_when_present(tmp_path: Path, monkeypatch) -> None:
    cwd = tmp_path / "scraped"
    cwd.mkdir()
    # Autouse fixture sets POLL_SECONDS=0 (loop never runs). For the
    # happy-path scrape, give it a tiny budget so the first iteration fires.
    monkeypatch.setattr(spawner, "RC_URL_POLL_SECONDS", 1.0)
    pane = "starting session...\nremote: https://rc.claude.com/abc123def\nbrief...\n"
    with patch("subprocess.run", side_effect=[_ok(), _ok(pane)]):
        result = spawn_background_lead(
            name="scraped", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    assert result["rc_url"] == "https://rc.claude.com/abc123def"


def test_spawn_returns_empty_rc_url_when_capture_fails(tmp_path: Path) -> None:
    cwd = tmp_path / "noscrape"
    cwd.mkdir()
    with patch(
        "subprocess.run",
        side_effect=[_ok(), sp.CalledProcessError(1, ["tmux"], stderr="no session")],
    ):
        result = spawn_background_lead(
            name="noscrape", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    assert result["rc_url"] == ""


def test_spawn_wraps_tmux_failure_as_runtime_error(tmp_path: Path) -> None:
    cwd = tmp_path / "boom"
    cwd.mkdir()
    err = sp.CalledProcessError(1, ["tmux"], output="", stderr="server died")
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match="server died"):
            spawn_background_lead(
                name="boom", brief="x", cwd=cwd, permission_mode="acceptEdits",
            )


def test_spawn_wraps_tmux_timeout_as_runtime_error(tmp_path: Path) -> None:
    cwd = tmp_path / "slow"
    cwd.mkdir()
    err = sp.TimeoutExpired(cmd=["tmux"], timeout=10)
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match="timed out"):
            spawn_background_lead(
                name="slow", brief="x", cwd=cwd, permission_mode="acceptEdits",
            )


def test_kill_session_stops_unit_then_kills_tmux_session() -> None:
    with patch("subprocess.run", return_value=_ok()) as run:
        kill_session("my-project")
    cmds = [c.args[0] for c in run.call_args_list]
    # First: stop the transient unit -- KillMode=control-group tears down the
    # whole cgroup (the tmux server with it).
    stop_cmd = cmds[0]
    assert any(a.endswith("systemctl") for a in stop_cmd)
    assert "stop" in stop_cmd
    assert "claude-soma-lead-my-project.service" in stop_cmd
    # Last: kill the session on its own socket (belt-and-suspenders).
    last = cmds[-1]
    assert last[0].endswith("tmux")
    assert "-L" in last
    assert last[last.index("-L") + 1] == "soma-lead-my-project"
    assert "kill-session" in last
    assert last[last.index("-t") + 1] == "soma-proj-my-project"


def test_kill_session_ignores_missing_session() -> None:
    err = sp.CalledProcessError(1, ["tmux"], stderr="can't find session")
    with patch("subprocess.run", side_effect=err):
        kill_session("ghost")


# --- new tests for the three V1.5 fixes ---

def test_spawn_pretrusts_cwd_in_claude_global_json(tmp_path: Path) -> None:
    """Spawn must add the cwd to ~/.claude.json with hasTrustDialogAccepted=true
    BEFORE launching tmux, so claude skips the safety-check dialog in the
    detached pane (where there's no human to hit Enter)."""
    cwd = tmp_path / "trustme"
    cwd.mkdir()
    global_json = Path(spawner._claude_global_json())
    assert not global_json.exists(), "fixture should give us a fresh path"

    with patch("subprocess.run", side_effect=[_ok(), _ok("")]):
        spawn_background_lead(
            name="trustme", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )

    data = json.loads(global_json.read_text())
    entry = data["projects"][str(cwd)]
    assert entry["hasTrustDialogAccepted"] is True
    assert "projectOnboardingSeenCount" in entry


def test_pretrust_merges_with_existing_projects(tmp_path: Path) -> None:
    """Pre-existing entries for OTHER projects must be preserved on write."""
    cwd = tmp_path / "newproj"
    cwd.mkdir()
    global_json = Path(spawner._claude_global_json())
    global_json.write_text(json.dumps({
        "projects": {
            "/some/other/cwd": {
                "hasTrustDialogAccepted": True,
                "allowedTools": ["Bash"],
            },
        },
        "theme": "dark",
    }))

    with patch("subprocess.run", side_effect=[_ok(), _ok("")]):
        spawn_background_lead(
            name="newproj", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )

    data = json.loads(global_json.read_text())
    assert data["theme"] == "dark"  # unrelated key preserved
    assert data["projects"]["/some/other/cwd"]["allowedTools"] == ["Bash"]  # other entry preserved
    assert data["projects"][str(cwd)]["hasTrustDialogAccepted"] is True  # new entry added


def test_pretrust_tolerates_corrupt_global_json(tmp_path: Path) -> None:
    """If ~/.claude.json is unreadable/corrupt, don't crash — just skip the
    pretrust step. Operator will see the dialog in the pane and can fix."""
    cwd = tmp_path / "corrupt"
    cwd.mkdir()
    global_json = Path(spawner._claude_global_json())
    global_json.write_text("{not valid json")

    with patch("subprocess.run", side_effect=[_ok(), _ok("")]):
        # Should NOT raise.
        spawn_background_lead(
            name="corrupt", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    # File left untouched (we bailed before writing).
    assert global_json.read_text() == "{not valid json"


def test_spawn_passes_remote_control_with_session_name(tmp_path: Path) -> None:
    """Project leads need --remote-control so they (a) stay alive after the
    first prompt completes, (b) get an rc.claude.com URL the operator can
    attach to from the Claude mobile app."""
    cwd = tmp_path / "rc"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="rc", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--remote-control" in args
    rc_idx = args.index("--remote-control") + 1
    # The RC name matches the tmux session name for easy correlation.
    assert args[rc_idx] == "soma-proj-rc"


def test_spawn_passes_setting_sources_excluding_user(tmp_path: Path) -> None:
    """Project leads must skip user-scope settings so the user-enabled telegram
    plugin doesn't load and steal the bot's Telegram poller slot (race
    documented in docs/notes/2026-05-25-telegram-poller-race.md)."""
    cwd = tmp_path / "ss"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="ss", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--setting-sources" in args
    ss_idx = args.index("--setting-sources") + 1
    assert args[ss_idx] == "project,local"


def test_capture_rc_url_polls_until_url_appears(tmp_path: Path, monkeypatch) -> None:
    """_capture_rc_url's retry loop: if the URL isn't in the pane on the first
    capture but shows up by the second, we still get it."""
    cwd = tmp_path / "polly"
    cwd.mkdir()
    # Re-enable a tiny poll budget for this test (autouse fixture sets it to 0).
    monkeypatch.setattr(spawner, "RC_URL_POLL_SECONDS", 1.0)
    monkeypatch.setattr(spawner, "RC_URL_POLL_INTERVAL", 0.0)

    # Sequence: tmux new-session, then capture-pane returns no URL, then
    # capture-pane returns the URL on the second poll.
    first_pane = "loading...\n"
    second_pane = "loaded\nremote: https://rc.claude.com/poll-success\n"
    with patch(
        "subprocess.run",
        side_effect=[_ok(), _ok(first_pane), _ok(second_pane)],
    ):
        result = spawn_background_lead(
            name="polly", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    assert result["rc_url"] == "https://rc.claude.com/poll-success"


def test_spawn_chains_pipe_pane_logging_to_lead_log(tmp_path: Path, monkeypatch) -> None:
    """The spawn must chain a `pipe-pane` (via tmux's `;` separator) in the SAME
    invocation, teeing the lead's pane to <log dir>/<name>.log so a dead lead's
    output survives. Same invocation => no extra spawn subprocess; the `cat`
    writer is forked by the tmux server (lead's cgroup) so logging survives a
    channel restart."""
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(log_dir))
    cwd = tmp_path / "loggy"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="loggy", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    # pipe-pane is chained after the new-session command via the `;` separator.
    assert ";" in args, args
    chain = args[args.index(";") + 1:]
    assert chain[0] == "pipe-pane"
    assert "-O" in chain and "-o" in chain  # output-only, idempotent
    assert chain[chain.index("-t") + 1] == "soma-proj-loggy"
    # The tee target is the per-lead log under the configured dir.
    assert any(str(log_dir / "loggy.log") in a for a in chain), chain
    # spawn created the log dir (best-effort mkdir).
    assert log_dir.is_dir()


def test_spawn_pipe_pane_logging_is_best_effort_on_unwritable_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """If the log dir can't be created, spawn must still succeed (logging is
    non-critical). The pipe-pane chain is still appended -- its `cat` simply
    exits without writing, leaving the pane untouched."""
    # Point the log dir under a *file*, so mkdir(parents=True) raises.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(blocker / "logs"))
    cwd = tmp_path / "stillspawns"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        result = spawn_background_lead(
            name="stillspawns", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    assert result["agent_id"] == "soma-proj-stillspawns"
    assert ";" in run.call_args_list[0][0][0]  # chain still present
