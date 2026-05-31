"""
Tests for scripts/orchestrator_gate.sh.

Pipes mock PreToolUse event JSON to the script via stdin and asserts:
  - DENY: exit 0 + stdout JSON with permissionDecision="deny"
  - ALLOW: exit 0 + stdout empty
"""

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

GATE = Path(__file__).parent.parent / "scripts" / "orchestrator_gate.sh"


def _run(event: dict, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(GATE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )


def _assert_deny(result: subprocess.CompletedProcess, contains: str | None = None) -> None:
    assert result.returncode == 0, f"expected exit 0, got {result.returncode}: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    if contains:
        assert contains in data["hookSpecificOutput"]["permissionDecisionReason"]


def _assert_allow(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, f"expected exit 0, got {result.returncode}: {result.stderr}"
    assert result.stdout.strip() == "", f"expected empty stdout, got: {result.stdout!r}"


# =========================================================================
# Tool-name-level DENY cases
# =========================================================================

@pytest.mark.parametrize("tool_name,tool_input", [
    ("Edit", {"file_path": "/x"}),
    ("Write", {"file_path": "/opt/claude-soma/config.json"}),
    ("NotebookEdit", {"file_path": "/x"}),
    ("Skill", {"skill": "x"}),
    ("WebFetch", {"url": "https://example.com"}),
    ("WebSearch", {"query": "find me something"}),
    ("mcp__playwright__browser_navigate", {}),
    ("mcp__playwright-linkedin__browser_click", {}),
    ("mcp__playwright-medium__browser_snapshot", {}),
    ("mcp__playwright-x__browser_type", {}),
    ("mcp__playwright-x-article__browser_take_screenshot", {}),
    ("mcp__claude_ai_Canva__search-designs", {}),
    ("mcp__claude_ai_Gmail__search_threads", {}),
    ("mcp__claude_ai_Google_Calendar__authenticate", {}),
    ("mcp__claude_ai_Google_Drive__authenticate", {}),
    ("mcp__huggingface__gr1_z_image_turbo_generate", {}),
    ("mcp__huggingface__dynamic_space", {}),
])
def test_tool_level_deny(tool_name: str, tool_input: dict) -> None:
    r = _run({"tool_name": tool_name, "tool_input": tool_input})
    _assert_deny(r)


# =========================================================================
# Bash-pattern DENY cases
# =========================================================================

@pytest.mark.parametrize("command", [
    # Package installs
    "sudo apt-get install jq",
    "apt install curl",
    "apt-get update",
    "pip install requests",
    "pip3 install fastapi",
    "pipx install black",
    "npm install express",
    "npm i lodash",
    "pnpm install",
    "pnpm add react",
    "yarn install",
    "yarn add axios",
    "cargo build --release",
    "cargo install ripgrep",
    "cargo test",
    "bun install",
    # Network git
    "git push origin main",
    "git clone https://github.com/foo/bar",
    "git pull origin main",
    "git fetch --depth=1 origin",
    # Builds / tests
    "docker build .",
    "docker run -it ubuntu bash",
    "make all",
    "make -j4",
    "cmake ..",
    "pytest -v",
    "pytest tests/",
    "npm test",
    "pnpm test",
    # Heavy compute
    "codex --image-gen 'a cat'",
    "ffmpeg -i input.mp4 output.mp3",
    "whisper-cli -f audio.wav",
    # Network curl/wget (non-localhost)
    "curl https://example.com",
    "wget https://example.com/file.tar.gz",
    "curl -O https://releases.example.com/v1.tar.gz",
])
def test_bash_deny(command: str) -> None:
    r = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    _assert_deny(r)


# =========================================================================
# Tool-name-level ALLOW cases
# =========================================================================

@pytest.mark.parametrize("tool_name,tool_input", [
    ("Read", {"file_path": "/etc/hostname"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Grep", {"pattern": "def main", "path": "."}),
    ("Agent", {"description": "x", "prompt": "y"}),
    ("TaskCreate", {"title": "x"}),
    ("TaskUpdate", {"task_id": "1", "status": "completed"}),
    ("TaskList", {}),
    ("TaskGet", {"task_id": "1"}),
    ("TaskStop", {"task_id": "1"}),
    ("SendMessage", {"to": "soma-proj-x", "message": "hi"}),
    ("AskUserQuestion", {"question": "which?"}),
    ("SendUserFile", {"files": ["/tmp/x.png"]}),
    ("ToolSearch", {"query": "select:Read"}),
    ("mcp__plugin_telegram_telegram__reply", {}),
    ("mcp__plugin_telegram_telegram__send_message", {}),
    ("mcp__voice-stt__transcribe", {}),
    ("mcp__voice-tts__synthesize", {}),
    ("mcp__project_orchestrator__spawn_project", {}),
    ("mcp__project_orchestrator__list_projects", {}),
    ("mcp__project_orchestrator__kill_project", {}),
    ("mcp__hermes_api__list_projects", {}),
    ("mcp__sequential-thinking__sequentialthinking", {}),
    # HuggingFace lookups (NOT the blocked ones)
    ("mcp__huggingface__hf_doc_search", {}),
    ("mcp__huggingface__hf_hub_query", {}),
    ("mcp__huggingface__paper_search", {}),
])
def test_tool_level_allow(tool_name: str, tool_input: dict) -> None:
    r = _run({"tool_name": tool_name, "tool_input": tool_input})
    _assert_allow(r)


# =========================================================================
# Bash-pattern ALLOW cases
# =========================================================================

@pytest.mark.parametrize("command", [
    # tmux inspection (send + capture)
    "tmux -L soma-lead-foo capture-pane -p -t soma-proj-foo",
    "tmux -L soma-lead-foo send-keys -t soma-proj-foo -l 'hello' && tmux -L soma-lead-foo send-keys -t soma-proj-foo Enter",
    # git read-only
    "git log --oneline -5",
    "git status",
    "git diff HEAD",
    "git show HEAD",
    "git rev-parse HEAD",
    "git branch -r",
    "git remote -v",
    # systemctl status queries
    "systemctl is-active claude-soma-channel.service",
    "systemctl status claude-soma-channel.service",
    "systemctl show claude-soma-channel.service",
    # curl / wget to localhost
    "curl -s http://127.0.0.1:9000/api/projects",
    "curl http://localhost:8080/health",
    "curl -X POST http://127.0.0.1:9000/api/admin/pause-all",
    # process / system inspection
    "ps aux | grep claude",
    "pgrep -f claude",
    "ls /opt/claude-soma/",
    "ls -la /tmp/",
    "wc -l /opt/claude-soma/registry.sqlite",
    "df -h",
    "free -m",
    "uptime",
    "date",
    "pwd",
    "which python3",
    "realpath scripts/channel-claude.sh",
    # file reading
    "cat /etc/hostname",
    "head -20 /var/log/syslog",
    "tail -50 ~/.claude-soma/activity.jsonl",
    "grep 'ERROR' /var/log/claude-soma/channel.log",
    # sqlite select
    "sqlite3 /opt/claude-soma/registry.sqlite 'SELECT * FROM projects'",
    # journalctl
    "journalctl --since '5 minutes ago' -u claude-soma-channel",
    # echo / printf
    "echo hello",
    "printf '%s\\n' hello",
])
def test_bash_allow(command: str) -> None:
    r = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    _assert_allow(r)


# =========================================================================
# Special: bypass env var
# =========================================================================

def test_bypass_env_var_allows_edit() -> None:
    # Even a normally-blocked tool passes through when bypass is set
    r = _run(
        {"tool_name": "Edit", "tool_input": {"file_path": "/x"}},
        env_overrides={"SOMA_ORCHESTRATOR_GATE_DISABLED": "1"},
    )
    _assert_allow(r)


def test_bypass_env_var_allows_bash_apt() -> None:
    r = _run(
        {"tool_name": "Bash", "tool_input": {"command": "apt-get install jq"}},
        env_overrides={"SOMA_ORCHESTRATOR_GATE_DISABLED": "1"},
    )
    _assert_allow(r)


# =========================================================================
# Special: fail-open cases
# =========================================================================

def test_fail_open_broken_jq() -> None:
    # Put a broken jq stub first in PATH; script should exit 0 with no output
    tmpdir = tempfile.mkdtemp()
    jq_stub = Path(tmpdir) / "jq"
    jq_stub.write_text("#!/bin/sh\nexit 1\n")
    jq_stub.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    existing_path = os.environ.get("PATH", "/usr/bin:/bin")
    r = _run(
        {"tool_name": "Edit", "tool_input": {"file_path": "/x"}},
        env_overrides={"PATH": f"{tmpdir}:{existing_path}"},
    )
    _assert_allow(r)


def test_fail_open_malformed_json() -> None:
    # Non-JSON stdin: script should exit 0 with no output
    env = os.environ.copy()
    result = subprocess.run(
        [str(GATE)],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    _assert_allow(result)


def test_fail_open_empty_stdin() -> None:
    # Empty stdin: TOOL should be empty, exit 0 with no output
    env = os.environ.copy()
    result = subprocess.run(
        [str(GATE)],
        input="",
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    _assert_allow(result)


# =========================================================================
# Deny reason contains dispatch instruction
# =========================================================================

def test_deny_reason_mentions_agent() -> None:
    r = _run({"tool_name": "Edit", "tool_input": {"file_path": "/x"}})
    _assert_deny(r, contains="Agent")


def test_deny_reason_mentions_responsive_bot() -> None:
    r = _run({"tool_name": "WebFetch", "tool_input": {"url": "https://x.com"}})
    _assert_deny(r, contains="responsive_bot.md")


def test_deny_bash_reason_mentions_agent() -> None:
    r = _run({"tool_name": "Bash", "tool_input": {"command": "pytest -v tests/"}})
    _assert_deny(r, contains="Agent")


# =========================================================================
# Heredoc body false-positive regression (fix a)
# =========================================================================

def test_heredoc_body_apt_install_is_allowed() -> None:
    # heredoc body mentions apt install but the actual command is cat
    command = "cat <<'EOF'\napt install curl\nEOF"
    r = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    _assert_allow(r)


def test_heredoc_body_pip_install_is_allowed() -> None:
    # heredoc body mentions pip install inside a script being written to a file
    command = "cat > /tmp/setup.sh <<'EOF'\npip install requests\nEOF"
    r = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    _assert_allow(r)


def test_heredoc_body_git_push_is_allowed() -> None:
    # heredoc body describes a git push command but is just documentation
    command = "tee /tmp/notes.txt <<'EOF'\ngit push origin main\nEOF"
    r = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    _assert_allow(r)


def test_bare_apt_install_is_still_denied() -> None:
    # direct apt install (no heredoc) must remain blocked
    r = _run({"tool_name": "Bash", "tool_input": {"command": "apt install curl"}})
    _assert_deny(r)


# =========================================================================
# Write path-scoping (fix b)
# =========================================================================

@pytest.mark.parametrize("file_path", [
    "/opt/claude-soma/registry.sqlite",
    "/opt/claude-soma/subdir/config.json",
    "/etc/claude-soma/secrets.env",
    "/etc/hosts",
    "/var/lib/claude-soma/state.db",
    "/var/lib/anything/foo",
])
def test_write_production_path_denied(file_path: str) -> None:
    r = _run({"tool_name": "Write", "tool_input": {"file_path": file_path}})
    _assert_deny(r)


@pytest.mark.parametrize("file_path", [
    "/tmp/foo.txt",
    "/tmp/scratch/output.json",
    "/tmp/S1-FI-GATE-MERGE-STOP.md",
])
def test_write_tmp_path_allowed(file_path: str) -> None:
    r = _run({"tool_name": "Write", "tool_input": {"file_path": file_path}})
    _assert_allow(r)


def test_write_missing_file_path_denied() -> None:
    # Write with no file_path should still deny (fail-safe)
    r = _run({"tool_name": "Write", "tool_input": {}})
    _assert_deny(r)
