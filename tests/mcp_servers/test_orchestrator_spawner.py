# tests/mcp_servers/test_orchestrator_spawner.py
from __future__ import annotations

import subprocess as sp
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator.spawner import (
    spawn_background_lead, BriefTooLong, InvalidProjectName, kill_session
)


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
    assert args[0].endswith("tmux")
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
    assert args[-1] == "Build it."
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


def test_spawn_scrapes_rc_url_when_present(tmp_path: Path) -> None:
    cwd = tmp_path / "scraped"
    cwd.mkdir()
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


def test_kill_session_runs_tmux_kill_session() -> None:
    with patch("subprocess.run", return_value=_ok()) as run:
        kill_session("my-project")
    args = run.call_args_list[0][0][0]
    assert args[0].endswith("tmux")
    assert "kill-session" in args
    assert "-t" in args
    t_idx = args.index("-t") + 1
    assert "my-project" in args[t_idx]


def test_kill_session_ignores_missing_session() -> None:
    err = sp.CalledProcessError(1, ["tmux"], stderr="can't find session")
    with patch("subprocess.run", side_effect=err):
        kill_session("ghost")
