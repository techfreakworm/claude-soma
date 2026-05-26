"""Behavioural tests for scripts/claude-safe.sh.

The wrapper must make a bare interactive `claude` safe (skip the user-scope
telegram plugin that would hijack the live bot poller) while leaving the bot's
own `--channels` invocation and management subcommands untouched. See
docs/KNOWN_BUGS.md entry #1.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "claude-safe.sh"

STUB = """#!/usr/bin/env bash
{
  printf 'ARG:%s\\n' "$@"
  printf 'TG:%s\\n' "${TELEGRAM_STATE_DIR:-__UNSET__}"
} > "$STUB_OUT"
"""


def _run(tmp_path: Path, *args: str) -> tuple[list[str], str]:
    """Run the wrapper with a recording stub standing in for real claude.

    Returns (argv the stub received, TELEGRAM_STATE_DIR the stub saw).
    """
    stub = tmp_path / "claude-stub"
    stub.write_text(STUB)
    stub.chmod(0o755)
    out = tmp_path / "stub.out"

    env = {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_SAFE_REAL": str(stub),
        "STUB_OUT": str(out),
        "TMPDIR": str(tmp_path),
        "HOME": str(tmp_path),
    }
    res = subprocess.run(
        ["bash", str(WRAPPER), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    lines = out.read_text().splitlines()
    argv = [ln[len("ARG:"):] for ln in lines if ln.startswith("ARG:")]
    tg = next((ln[len("TG:"):] for ln in lines if ln.startswith("TG:")), "__UNSET__")
    return argv, tg


def test_bare_session_gets_plugin_skip_and_throwaway_state_dir(tmp_path):
    argv, tg = _run(tmp_path, "reply just hello")
    assert argv[:2] == ["--setting-sources", "project,local"]
    assert argv[2] == "reply just hello"
    # Throwaway, private state dir -- never the real one.
    assert tg != "__UNSET__"
    assert tg.startswith(str(tmp_path))
    assert "/.claude/channels/telegram" not in tg


def test_channels_invocation_is_passed_through_untouched(tmp_path):
    argv, tg = _run(
        tmp_path, "--channels", "plugin:telegram@claude-plugins-official",
        "--dangerously-skip-permissions",
    )
    # The bot MUST keep loading the plugin: no injection, no state-dir override.
    assert argv == [
        "--channels", "plugin:telegram@claude-plugins-official",
        "--dangerously-skip-permissions",
    ]
    assert tg == "__UNSET__"


def test_existing_setting_sources_is_respected(tmp_path):
    argv, _ = _run(tmp_path, "--setting-sources", "user,project", "do it")
    # Wrapper must not inject a second --setting-sources.
    assert argv.count("--setting-sources") == 1
    assert argv == ["--setting-sources", "user,project", "do it"]


def test_management_subcommand_is_passed_through(tmp_path):
    argv, tg = _run(tmp_path, "plugin", "list")
    assert argv == ["plugin", "list"]
    assert tg == "__UNSET__"


@pytest.mark.parametrize("flag", ["--version", "--help"])
def test_info_flags_are_passed_through(tmp_path, flag):
    argv, _ = _run(tmp_path, flag)
    assert argv == [flag]
