# src/claude_soma/mcp_servers/project_orchestrator/spawner.py
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


CLAUDE_BIN = os.environ.get("HERMES_CLAUDE_BIN", "claude")
MAX_BRIEF_CHARS = 100_000  # safety: keep briefs reasonable
NAME_RX = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class BriefTooLong(Exception):
    pass


class InvalidProjectName(Exception):
    pass


def _claude() -> str:
    if Path(CLAUDE_BIN).exists():
        return CLAUDE_BIN
    found = shutil.which(CLAUDE_BIN)
    if found is None:
        raise RuntimeError(f"claude binary not found ({CLAUDE_BIN})")
    return found


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

    cmd: list[str] = [
        _claude(), "--bg",
        "--name", name,
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--output-format", "json",
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(brief)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"claude --bg failed for {name!r}: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude --bg timed out spawning {name!r} (60s)") from e

    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as e:
        raise RuntimeError(
            f"claude --bg returned non-JSON stdout: {result.stdout[-500:]!r}"
        ) from e

    return {
        "agent_id": payload.get("agent_id") or payload.get("session_id"),
        "rc_url": payload.get("rc_url") or payload.get("claude_code_session_url", ""),
        "cwd": str(cwd),
    }
