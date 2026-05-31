# tests/mcp_servers/test_orchestrator_spawner.py
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator import spawner
from claude_soma.mcp_servers.project_orchestrator.spawner import (
    spawn_background_lead, resume_background_lead,
    BriefTooLong, InvalidProjectName, kill_session,
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
    # Point the lead MCP config at a NON-existent path by default so --mcp-config
    # is deterministically omitted (tests that exercise it set their own file).
    monkeypatch.setenv("HERMES_LEAD_MCP_CONFIG", str(tmp_path / "absent-lead-mcp.json"))


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


def test_is_lead_alive_true_when_session_present() -> None:
    with patch("subprocess.run", return_value=_ok()) as run:
        assert spawner.is_lead_alive("hello") is True
    cmd = run.call_args_list[0][0][0]
    assert cmd[0].endswith("tmux")
    assert cmd[cmd.index("-L") + 1] == "soma-lead-hello"
    assert "has-session" in cmd
    assert cmd[cmd.index("-t") + 1] == "soma-proj-hello"


def test_is_lead_alive_false_when_session_gone() -> None:
    gone = _ok()
    gone.returncode = 1  # tmux has-session exits non-zero when it's gone
    with patch("subprocess.run", return_value=gone):
        assert spawner.is_lead_alive("hello") is False


def test_is_lead_alive_accepts_agent_id_form() -> None:
    """is_lead_alive must accept either a bare name or the soma-proj-<name>
    agent_id and resolve to the same socket/session."""
    with patch("subprocess.run", return_value=_ok()) as run:
        assert spawner.is_lead_alive("soma-proj-hello") is True
    cmd = run.call_args_list[0][0][0]
    assert cmd[cmd.index("-L") + 1] == "soma-lead-hello"
    assert cmd[cmd.index("-t") + 1] == "soma-proj-hello"


def test_is_lead_alive_conservative_on_tool_error() -> None:
    """If the check itself can't run, assume alive -- a false 'dead' would hide
    a running lead and risk a duplicate respawn."""
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd=["tmux"], timeout=10)):
        assert spawner.is_lead_alive("hello") is True


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


def test_spawn_passes_setting_sources_including_user(tmp_path: Path) -> None:
    """Leads load user,project,local so they inherit user-scope MCPs
    (sequential-thinking) + the user's skills/plugins. Telegram is no longer in
    user scope (bot opts in via --settings), so including user no longer risks
    the poller hijack -- see docs/notes/2026-05-26-leads-inherit-all-mcps.md."""
    cwd = tmp_path / "ss"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="ss", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--setting-sources" in args
    ss_idx = args.index("--setting-sources") + 1
    assert args[ss_idx] == "user,project,local"
    # The bot's --settings (telegram opt-in) is NEVER passed to a lead.
    assert "--settings" not in args


def test_spawn_injects_lead_mcp_config_when_present(tmp_path: Path, monkeypatch) -> None:
    """When the curated lead MCP config exists, spawn passes it via --mcp-config
    so leads get the bot's tool servers (playwright, voice)."""
    cfg = tmp_path / "lead-mcp.json"
    cfg.write_text('{"mcpServers": {}}')
    monkeypatch.setenv("HERMES_LEAD_MCP_CONFIG", str(cfg))
    cwd = tmp_path / "mc"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="mc", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--mcp-config" in args
    assert args[args.index("--mcp-config") + 1] == str(cfg)


def test_spawn_omits_mcp_config_when_absent(tmp_path: Path, monkeypatch) -> None:
    """If the lead MCP config file is missing, --mcp-config is omitted (the lead
    still spawns; it just falls back to its own scopes) rather than failing."""
    monkeypatch.setenv("HERMES_LEAD_MCP_CONFIG", str(tmp_path / "does-not-exist.json"))
    cwd = tmp_path / "nomc"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="nomc", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--mcp-config" not in args


def test_brief_is_guarded_by_dashdash_after_mcp_config(tmp_path: Path, monkeypatch) -> None:
    """Regression (commit 6ac7ed2): claude's --mcp-config is variadic
    ("<configs...>"), so a bare trailing brief right after `--mcp-config <path>`
    is swallowed as a SECOND config-file path -> ENAMETOOLONG -> the lead crashes
    at startup. The brief must be preceded by a `--` end-of-options separator and
    must never be the token immediately following --mcp-config."""
    cfg = tmp_path / "lead-mcp.json"
    cfg.write_text('{"mcpServers": {}}')
    monkeypatch.setenv("HERMES_LEAD_MCP_CONFIG", str(cfg))
    cwd = tmp_path / "g"
    cwd.mkdir()
    brief = "You are the lead; do the thing."
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(name="g", brief=brief, cwd=cwd, permission_mode="acceptEdits")
    args = run.call_args_list[0][0][0]
    assert "--mcp-config" in args
    # --mcp-config is followed by the config PATH, not the brief.
    assert args[args.index("--mcp-config") + 1] == str(cfg)
    # The brief is the positional prompt, guarded by `--`.
    assert args[args.index(brief) - 1] == "--"


def test_brief_is_guarded_by_dashdash_without_mcp_config(tmp_path: Path, monkeypatch) -> None:
    """The `--` guard is present even when --mcp-config is omitted, so a brief
    that starts with '-' can't be misparsed as a flag."""
    monkeypatch.setenv("HERMES_LEAD_MCP_CONFIG", str(tmp_path / "absent.json"))
    cwd = tmp_path / "g2"
    cwd.mkdir()
    brief = "-x is a weird brief"
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(name="g2", brief=brief, cwd=cwd, permission_mode="acceptEdits")
    args = run.call_args_list[0][0][0]
    assert "--mcp-config" not in args
    assert args[args.index(brief) - 1] == "--"


def test_spawn_includes_continue_and_session_id_before_remote_control(tmp_path: Path) -> None:
    """spawn_background_lead must pass --continue AND --session-id <uuid> before
    --remote-control. --continue resumes local transcript on unit restart;
    --session-id names the cloud session for future --resume retrieval.
    Ordering: --continue, --session-id, <uuid>, --remote-control."""
    cwd = tmp_path / "cont"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="cont", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--continue" in args
    assert "--session-id" in args
    assert "--remote-control" in args
    cont_idx = args.index("--continue")
    sid_idx = args.index("--session-id")
    rc_idx = args.index("--remote-control")
    # --continue comes first, then --session-id <uuid>, then --remote-control
    assert cont_idx < sid_idx < rc_idx, (
        f"expected --continue ({cont_idx}) < --session-id ({sid_idx}) "
        f"< --remote-control ({rc_idx})"
    )
    # --session-id is immediately followed by the UUID value
    assert sid_idx + 1 < rc_idx


def test_spawn_injects_hermes_lead_name_and_notify_endpoint(tmp_path: Path) -> None:
    """_wrap_in_transient_unit must inject HERMES_LEAD_NAME and HERMES_NOTIFY_ENDPOINT
    so the lead's hermes-notify MCP tool can identify itself and reach the listener."""
    cwd = tmp_path / "fi-lead"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        spawn_background_lead(
            name="fi-lead", brief="notify test", cwd=cwd,
            permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--setenv=HERMES_LEAD_NAME=fi-lead" in args
    assert "--setenv=HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100" in args


def test_lead_mcp_config_is_curated_tool_set() -> None:
    """Drift guard for the shipped config/claude/lead-mcp.json:

    - the TOOL servers must equal the bot's .mcp.json MINUS the control-plane
      ones (hermes-api clobbers the dashboard socket; project-orchestrator
      shares the registry / could recursively spawn leads),
    - lead-only additions NOT in .mcp.json: sequential-thinking (user-scope
      loading is cwd/timing flaky, so we guarantee it via --mcp-config) and
      huggingface (hosted HF MCP: docs semantic-search + Hub tools, for leads),
    - the control-plane servers and telegram are never present.
    Each MIRRORED tool stanza must match .mcp.json byte-for-byte (catches path
    drift)."""
    import json
    repo = Path(__file__).resolve().parents[2]
    full = json.loads((repo / ".mcp.json").read_text())["mcpServers"]
    lead = json.loads((repo / "config/claude/lead-mcp.json").read_text())["mcpServers"]
    assert "hermes-api" not in lead and "project-orchestrator" not in lead
    assert "telegram" not in lead and not any("telegram" in k for k in lead)
    # Lead-only extras (present in lead-mcp.json, intentionally absent from .mcp.json).
    # hermes-notify is lead-only: leads push events via it; the bot resolves events
    # through mcp__hermes_api__resolve_pending_input instead (blast radius control).
    lead_only = {"sequential-thinking", "huggingface", "hermes-notify"}
    assert lead_only <= set(lead)
    mirrored = set(lead) - lead_only
    assert mirrored == set(full) - {"hermes-api", "project-orchestrator"}
    for name in mirrored:
        assert lead[name] == full[name], f"{name} drifted from .mcp.json"


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


def test_spawn_scrapes_claude_ai_rc_url(tmp_path: Path, monkeypatch) -> None:
    """The live Remote Control URL format is https://claude.ai/code/session_<id>
    (verified across three running leads 2026-05-26). The old regex only knew
    rc.claude.com, so every real spawn returned rc_url="" even though the pane
    showed a valid URL."""
    cwd = tmp_path / "claudeai"
    cwd.mkdir()
    # Autouse fixture zeroes the poll budget; give one iteration so it captures.
    monkeypatch.setattr(spawner, "RC_URL_POLL_SECONDS", 1.0)
    pane = (
        "  https://claude.ai/code/session_018tGGH6weoRDokVSn7fmMhR\n"
        "  bypass permissions on (shift+tab to cycle)\n"
        "                                Remote Control active\n"
    )
    with patch("subprocess.run", side_effect=[_ok(), _ok(pane)]):
        result = spawn_background_lead(
            name="claudeai", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    assert result["rc_url"] == "https://claude.ai/code/session_018tGGH6weoRDokVSn7fmMhR"


def test_rc_url_rx_bounds_match_and_keeps_legacy_fallback() -> None:
    """RC_URL_RX must (a) capture the live claude.ai URL cleanly even when a
    box-drawing glyph abuts it (a bare \\S+ would swallow the glyph), (b) still
    accept the legacy rc.claude.com URL, and (c) NOT match the literal
    `session_<id>` placeholder that appears verbatim in some briefs/TASK files."""
    rx = spawner.RC_URL_RX
    live = "https://claude.ai/code/session_018tGGH6weoRDokVSn7fmMhR"
    # Glyph immediately adjacent (no space): bounded class stops at the glyph.
    m = rx.search(f"Remote Control · {live}│")
    assert m is not None and m.group(0) == live
    legacy = "https://rc.claude.com/abc123def"
    assert rx.search(f"remote: {legacy}\n").group(0) == legacy
    # `<` is not a session-id character, so the placeholder must not match.
    assert rx.search("the URL is https://claude.ai/code/session_<id> now") is None


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


def test_discover_team_returns_panes_beyond_lead() -> None:
    """A lead's agent-team teammates show up as split panes; discover_team reads
    them live, treating the first pane as the lead and the rest as teammates,
    with status from pane_dead."""
    out = "0\t0\tlead pane\n1\t0\twriter working\n2\t1\tposter done\n"
    with patch("subprocess.run", return_value=_ok(out)) as run:
        team = spawner.discover_team("demoteam")
    cmd = run.call_args_list[0][0][0]
    assert cmd[cmd.index("-L") + 1] == "soma-lead-demoteam"
    assert "list-panes" in cmd
    assert [m["handle"] for m in team] == ["teammate-1", "teammate-2"]
    assert team[0] == {"handle": "teammate-1", "role": "writer working", "status": "active"}
    assert team[1]["status"] == "dead"  # pane_dead=1


def test_discover_team_empty_when_only_lead_pane() -> None:
    with patch("subprocess.run", return_value=_ok("0\t0\tjust the lead\n")):
        assert spawner.discover_team("solo") == []


def test_discover_team_empty_on_dead_session_or_error() -> None:
    gone = _ok("")
    gone.returncode = 1
    with patch("subprocess.run", return_value=gone):
        assert spawner.discover_team("ghost") == []
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd=["tmux"], timeout=10)):
        assert spawner.discover_team("ghost") == []


# --- session-id / resume tests ---

def test_first_spawn_injects_session_id_flag(tmp_path: Path) -> None:
    """spawn_background_lead must pass --session-id <uuid> so claude records the
    session in the cloud under a stable ID, enabling --resume after a crash."""
    cwd = tmp_path / "sid"
    cwd.mkdir()
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        result = spawn_background_lead(
            name="sid", brief="x", cwd=cwd, permission_mode="acceptEdits",
        )
    args = run.call_args_list[0][0][0]
    assert "--session-id" in args
    # The value after --session-id is a UUID4 (32 hex digits + 4 hyphens = 36 chars)
    sid_value = args[args.index("--session-id") + 1]
    import re as _re
    uuid4_rx = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert uuid4_rx.match(sid_value), f"expected uuid4, got {sid_value!r}"
    # Result dict must expose the uuid so the registry can persist it.
    assert "session_uuid" in result
    assert result["session_uuid"] == sid_value


def test_first_spawn_accepts_explicit_session_uuid(tmp_path: Path) -> None:
    """Caller can supply a session_uuid (e.g. for testing); spawn must use it."""
    cwd = tmp_path / "expl"
    cwd.mkdir()
    fixed_uuid = "aaaabbbb-cccc-4ddd-eeee-ffffffffffff"
    with patch("subprocess.run", side_effect=[_ok(), _ok("")]) as run:
        result = spawn_background_lead(
            name="expl", brief="x", cwd=cwd, permission_mode="acceptEdits",
            session_uuid=fixed_uuid,
        )
    args = run.call_args_list[0][0][0]
    assert "--session-id" in args
    assert args[args.index("--session-id") + 1] == fixed_uuid
    assert result["session_uuid"] == fixed_uuid


def test_resume_spawn_uses_resume_flag_not_continue(tmp_path: Path) -> None:
    """resume_background_lead must pass --resume <uuid> and MUST NOT pass
    --continue or --session-id (those are first-spawn only).

    kill_session issues two subprocess calls (systemctl stop + tmux kill-session)
    before the new-session spawn, so the spawn call is the THIRD call.
    """
    cwd = tmp_path / "res"
    cwd.mkdir()
    fixed_uuid = "11112222-3333-4444-5555-666677778888"
    # kill_session: 2 calls (systemctl stop + tmux kill-session)
    # spawn:        1 call (tmux new-session via systemd-run)
    # capture_rc:   1 call (tmux capture-pane)
    with patch("subprocess.run", side_effect=[_ok(), _ok(), _ok(), _ok("")]) as run:
        result = resume_background_lead(
            name="res", cwd=cwd, permission_mode="acceptEdits",
            session_uuid=fixed_uuid,
        )
    # The spawn call is the THIRD subprocess.run: after systemctl stop and tmux kill-session.
    spawn_call = run.call_args_list[2][0][0]
    assert "--resume" in spawn_call
    assert spawn_call[spawn_call.index("--resume") + 1] == fixed_uuid
    assert "--continue" not in spawn_call
    assert "--session-id" not in spawn_call
    assert result["session_uuid"] == fixed_uuid


def test_resume_spawn_appends_team_suffix_to_prompt(tmp_path: Path) -> None:
    """When resume_prompt_suffix is supplied, the brief passed to claude must
    start with the fixed base prompt and end with the team roster suffix."""
    cwd = tmp_path / "res-team"
    cwd.mkdir()
    fixed_uuid = "aaaabbbb-cccc-4444-5555-111122223333"
    suffix = (
        "Before you were interrupted, your agent team included:\n"
        "- teammate-1 (role: PM): PM\n"
        "You may want to re-establish your team with the Agent tool."
    )
    # kill_session: 2 calls; spawn: 1 call; capture_rc: 1 call
    with patch("subprocess.run", side_effect=[_ok(), _ok(), _ok(), _ok("")]) as run:
        resume_background_lead(
            name="res-team", cwd=cwd, permission_mode="acceptEdits",
            session_uuid=fixed_uuid, resume_prompt_suffix=suffix,
        )
    spawn_call = run.call_args_list[2][0][0]
    # The final token before `;` is the brief passed to claude.
    sep_idx = spawn_call.index(";")
    brief_arg = spawn_call[sep_idx - 1]
    assert brief_arg.startswith("You have been resumed after an interruption.")
    assert "Before you were interrupted" in brief_arg
    assert "teammate-1" in brief_arg
    assert "re-establish your team" in brief_arg


def test_resume_spawn_no_suffix_uses_fixed_prompt_only(tmp_path: Path) -> None:
    """When resume_prompt_suffix is None the brief is exactly the fixed prompt
    with no trailing newlines or extra content."""
    cwd = tmp_path / "res-nosuffix"
    cwd.mkdir()
    fixed_uuid = "bbbbcccc-dddd-4444-eeee-111100002222"
    with patch("subprocess.run", side_effect=[_ok(), _ok(), _ok(), _ok("")]) as run:
        resume_background_lead(
            name="res-nosuffix", cwd=cwd, permission_mode="acceptEdits",
            session_uuid=fixed_uuid,
        )
    spawn_call = run.call_args_list[2][0][0]
    sep_idx = spawn_call.index(";")
    brief_arg = spawn_call[sep_idx - 1]
    assert brief_arg == (
        "You have been resumed after an interruption. "
        "Review your prior work in this session and continue from where you left off."
    )


def test_resume_spawn_calls_kill_session_first(tmp_path: Path) -> None:
    """resume_background_lead cleans up any lingering unit before spawning.
    kill_session issues systemctl stop + tmux kill-session; those must precede the
    new tmux new-session call."""
    cwd = tmp_path / "reskill"
    cwd.mkdir()
    fixed_uuid = "deadbeef-0000-4000-8000-000000000001"
    commands: list[list[str]] = []

    def _record_run(*args, **kwargs) -> MagicMock:
        cmd = args[0] if args else []
        commands.append(cmd)
        return _ok()

    with patch("subprocess.run", side_effect=_record_run):
        resume_background_lead(
            name="reskill", cwd=cwd, permission_mode="acceptEdits",
            session_uuid=fixed_uuid,
        )

    # First batch of calls is kill_session: systemctl stop + tmux kill-session
    systemctl_calls = [c for c in commands if any("systemctl" in a for a in c)]
    new_session_calls = [c for c in commands if "new-session" in c]
    assert systemctl_calls, "kill_session must have called systemctl stop"
    assert new_session_calls, "must have called tmux new-session for the resume spawn"
    # systemctl stop must precede new-session
    assert commands.index(systemctl_calls[0]) < commands.index(new_session_calls[0])
