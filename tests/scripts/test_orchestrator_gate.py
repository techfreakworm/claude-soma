"""Tests for scripts/orchestrator_gate.sh and scripts/orchestrator_gate.py."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "orchestrator_gate.sh"
_SCRIPT_PY = Path(__file__).parent.parent.parent / "scripts" / "orchestrator_gate.py"

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


def _gate_py(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(_SCRIPT_PY)],
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


# ===== V3 tests: Python shlex parser =====

_V3_ALLOW_CASES = [
    "npm view some-pkg test",
    'git log --grep="fetch this" --depth=5',
    "docker logs run-name",
    "FOO=bar",
    "cd /tmp/curl-output && ls",
    "curl http://127.0.0.1:9100/healthz",
    "curl http://localhost:9000/api/healthz",
]

_V3_DENY_CASES = [
    "npm install foo",
    "git clone https://x",
    "docker build .",
    'claude --print "hi"',
    'bash -c "curl https://x"',
    'eval "pip install foo"',
    "nohup curl https://x",
    "setsid wget https://y",
    "pytest tests/",
    'codex exec "..."',
]


@pytest.mark.parametrize("cmd", _V3_ALLOW_CASES)
def test_v3_allow(cmd: str) -> None:
    """V3 Python gate correctly allows safe commands (no false-positive denies)."""
    event = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    result = _gate_py(event)
    assert result.returncode == 0
    assert not _is_denied(result), f"Unexpectedly denied: {cmd!r}"


@pytest.mark.parametrize("cmd", _V3_DENY_CASES)
def test_v3_deny(cmd: str) -> None:
    """V3 Python gate correctly denies blocked commands including wrapper-bypass cases."""
    event = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    result = _gate_py(event)
    assert result.returncode == 0
    assert _is_denied(result), f"Expected deny but got allow: {cmd!r}"


def test_v3_fail_open_substitution() -> None:
    """Command substitution fails open (empty stdout) and writes to activity.jsonl."""
    import os
    import tempfile
    import json as _json

    with tempfile.TemporaryDirectory() as tmp_home:
        env = os.environ.copy()
        env["HOME"] = tmp_home
        event = {"tool_name": "Bash", "tool_input": {"command": "echo $(curl http://x)"}}
        result = subprocess.run(
            ["python3", str(_SCRIPT_PY)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        assert result.returncode == 0
        assert not _is_denied(result)
        activity_path = Path(tmp_home) / ".claude-soma" / "activity.jsonl"
        assert activity_path.exists(), "activity.jsonl not written by telemetry"
        entry = _json.loads(activity_path.read_text().strip())
        assert entry["source"] == "orchestrator_gate_v3"
        assert entry["action"] == "fail_open_substitution_or_parse_error"
