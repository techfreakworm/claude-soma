# src/claude_soma/mcp_servers/project_orchestrator/spawner.py
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
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

# Where Claude Code stores per-cwd trust state (NOT ~/.claude/settings.json —
# that key is ignored in 2.1.150; only this file is consulted). Override with
# HERMES_CLAUDE_GLOBAL_JSON for tests.
CLAUDE_GLOBAL_JSON_DEFAULT = str(Path.home() / ".claude.json")

MAX_BRIEF_CHARS = 100_000  # safety: keep briefs reasonable
NAME_RX = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
RC_URL_RX = re.compile(r"https?://rc\.claude\.com/\S+")

# How long to keep polling tmux capture for the rc.claude.com URL before
# giving up. claude prints the URL within ~5s usually, but the first capture
# right after tmux returns sometimes misses it.
RC_URL_POLL_SECONDS = 30
RC_URL_POLL_INTERVAL = 2


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


def _claude_global_json() -> Path:
    return Path(os.environ.get("HERMES_CLAUDE_GLOBAL_JSON", CLAUDE_GLOBAL_JSON_DEFAULT))


def _pretrust_cwd(cwd: Path) -> None:
    """Pre-mark `cwd` as trusted in ~/.claude.json so claude skips the
    "Quick safety check: is this a project you trust?" interactive dialog at
    startup. Without this, a tmux-detached claude blocks forever waiting for
    a human to hit Enter.

    Idempotent: existing project entries are preserved/merged. The whole file
    is rewritten atomically (tempfile + os.replace) to avoid a torn write that
    would corrupt other projects' state.
    """
    path = _claude_global_json()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        # Don't bulldoze a corrupt file; just bail and let the dialog appear
        # (operator will see the prompt in the tmux pane and can investigate).
        return

    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(cwd), {})
    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 0)

    # Atomic write: write to a sibling tempfile, fsync, rename. Preserves
    # the existing inode's owner/mode by recreating them after the rename.
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".claude.json.", suffix=".tmp", dir=str(parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _capture_rc_url(session: str, timeout: float | None = None) -> str:
    """Poll the tmux pane until the rc.claude.com URL appears, or timeout.

    The original implementation was a single capture-pane call right after
    tmux returned, which raced with claude's startup output — most of the
    time the URL hadn't printed yet at that exact instant and we silently
    returned "". Polling gives claude time to actually emit the URL.

    `timeout=None` reads the module constant at call time, so tests can
    monkeypatch RC_URL_POLL_SECONDS without rebinding the default.
    """
    if timeout is None:
        timeout = RC_URL_POLL_SECONDS
    deadline = time.monotonic() + timeout
    last_stdout = ""
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                [_tmux(), "capture-pane", "-p", "-t", session],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""
        last_stdout = result.stdout or ""
        match = RC_URL_RX.search(last_stdout)
        if match:
            return match.group(0)
        time.sleep(RC_URL_POLL_INTERVAL)
    return ""


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

    # Mark the cwd as trusted BEFORE spawning, otherwise claude blocks on the
    # safety dialog forever in a detached tmux pane (no human to hit Enter).
    _pretrust_cwd(cwd)

    session = _session_name(name)
    claude_argv: list[str] = [
        _claude(),
        # --remote-control <name>: project leads stay alive after their first
        # task completes (otherwise claude exits) AND get an rc.claude.com URL
        # the operator can attach to from the Claude mobile app / web. The name
        # matches our tmux session name so they're easy to correlate.
        "--remote-control", session,
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--dangerously-skip-permissions",
        "--effort", "max",
        # Skip user-scope settings so the user-enabled telegram plugin doesn't
        # load in project-lead sessions and hijack the bot's Telegram poller
        # slot. Confirmed race documented in
        # docs/notes/2026-05-25-telegram-poller-race.md. The bot itself runs
        # WITHOUT this flag so it keeps the user-scope plugin and continues
        # polling; claude.ai connectors (Canva, Gmail) are auth-driven via
        # ~/.claude/.credentials.json so project leads keep those.
        "--setting-sources", "project,local",
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
