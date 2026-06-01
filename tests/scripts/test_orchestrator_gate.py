"""Tests for scripts/orchestrator_gate.sh."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "orchestrator_gate.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="jq not installed"
)


def _gate(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=5,
    )


def _is_denied(result: subprocess.CompletedProcess) -> bool:
    if not result.stdout.strip():
        return False
    try:
        out = json.loads(result.stdout)
        return out["hookSpecificOutput"]["permissionDecision"] == "deny"
    except (json.JSONDecodeError, KeyError):
        return False


def test_claude_subprocess_is_denied() -> None:
    """Bash command 'claude -p hi' is denied."""
    event = {"tool_name": "Bash", "tool_input": {"command": "claude -p hi"}}
    result = _gate(event)
    assert result.returncode == 0
    assert _is_denied(result)


def test_claude_bare_is_denied() -> None:
    """Bare 'claude' command in Bash is denied."""
    event = {"tool_name": "Bash", "tool_input": {"command": "claude"}}
    result = _gate(event)
    assert result.returncode == 0
    assert _is_denied(result)


def test_safe_bash_is_allowed() -> None:
    """A harmless echo command in Bash passes through (no deny output)."""
    event = {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}
    result = _gate(event)
    assert result.returncode == 0
    assert not _is_denied(result)


def test_pytest_bash_is_denied() -> None:
    """'pytest' in a Bash command is denied (pre-existing rule regression)."""
    event = {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}}
    result = _gate(event)
    assert result.returncode == 0
    assert _is_denied(result)


def test_bypass_env_allows_claude() -> None:
    """SOMA_ORCHESTRATOR_GATE_DISABLED=1 bypasses the gate for any command."""
    import os

    env = os.environ.copy()
    env["SOMA_ORCHESTRATOR_GATE_DISABLED"] = "1"
    event = {"tool_name": "Bash", "tool_input": {"command": "claude -p hi"}}
    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    assert result.returncode == 0
    assert not _is_denied(result)
