# src/claude_soma/mcp_servers/project_orchestrator/spawner.py
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


# claude --bg was removed in Claude Code 2.1.150. We now wrap each project lead
# in a detached tmux session, matching the pattern used by
# systemd/claude-soma-channel.service. The native claude install at
# ~/.local/bin/claude is required because the npm wrap at /usr/bin/claude
# silently downgrades to --print mode for the flags we need.
NATIVE_CLAUDE_DEFAULT = "/home/ubuntu/.local/bin/claude"
CLAUDE_BIN = os.environ.get("HERMES_CLAUDE_BIN", NATIVE_CLAUDE_DEFAULT)
TMUX_BIN = os.environ.get("HERMES_TMUX_BIN", "/usr/bin/tmux")
TMUX_SESSION_PREFIX = "soma-proj-"

MAX_BRIEF_CHARS = 100_000  # safety: keep briefs reasonable
NAME_RX = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
RC_URL_RX = re.compile(r"https?://rc\.claude\.com/\S+")


class BriefTooLong(Exception):
    pass


class InvalidProjectName(Exception):
    pass


def _claude() -> str:
    if Path(CLAUDE_BIN).exists():
        return CLAUDE_BIN
    found = shutil.which(CLAUDE_BIN) or shutil.which("claude")
    if found is None:
        raise RuntimeError(
            f"claude binary not found (tried {CLAUDE_BIN!r} and PATH)"
        )
    return found


def _tmux() -> str:
    if Path(TMUX_BIN).exists():
        return TMUX_BIN
    found = shutil.which(TMUX_BIN) or shutil.which("tmux")
    if found is None:
        raise RuntimeError(f"tmux binary not found (tried {TMUX_BIN!r} and PATH)")
    return found


def _session_name(name: str) -> str:
    return f"{TMUX_SESSION_PREFIX}{name}"


def _capture_rc_url(session: str) -> str:
    try:
        result = subprocess.run(
            [_tmux(), "capture-pane", "-p", "-t", session],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    match = RC_URL_RX.search(result.stdout or "")
    return match.group(0) if match else ""


def spawn_background_lead(
    *,
    name: str,
    brief: str,
    cwd: Path,
    permission_mode: str,
    extra_args: list[str] | None = None,
) -> dict:
    if not NAME_RX.match(name):
        raise InvalidProjectName(
            f"project name must match {NAME_RX.pattern}, got {name!r}"
        )
    if len(brief) > MAX_BRIEF_CHARS:
        raise BriefTooLong(f"brief is {len(brief)} chars (max {MAX_BRIEF_CHARS})")
    cwd.mkdir(parents=True, exist_ok=True)

    session = _session_name(name)
    claude_argv: list[str] = [
        _claude(),
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--dangerously-skip-permissions",
        "--effort", "max",
    ]
    if extra_args:
        claude_argv.extend(extra_args)
    claude_argv.append(brief)

    cmd: list[str] = [
        _tmux(), "new-session", "-d", "-s", session, "-c", str(cwd),
        *claude_argv,
    ]

    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"tmux new-session failed for {name!r}: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"tmux new-session timed out for {name!r} (10s)") from e

    rc_url = _capture_rc_url(session)

    return {
        "agent_id": session,
        "rc_url": rc_url,
        "cwd": str(cwd),
    }


def kill_session(name: str) -> None:
    session = _session_name(name) if not name.startswith(TMUX_SESSION_PREFIX) else name
    try:
        subprocess.run(
            [_tmux(), "kill-session", "-t", session],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Already gone, or tmux server down — nothing to do.
        return
