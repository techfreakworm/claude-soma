"""Tests for scripts/channel_clear.sh.

All tests inject a fake 'tmux' at the front of PATH so no real tmux session
is touched. CHANNEL_CLEAR_LOG is redirected to a temp file, and
CHANNEL_CLEAR_SLEEP is set to 0 to keep the suite fast.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "channel_clear.sh"
_REPO_ROOT = Path(__file__).parent.parent.parent


def _make_fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _run_script(
    tmp_path: Path,
    call_log: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    log = call_log or (tmp_path / "tmux-calls.log")
    _make_fake_bin(tmp_path, "tmux", f'echo "tmux $*" >> "{log}"')

    clear_log = tmp_path / "channel-clear.log"

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["CHANNEL_CLEAR_LOG"] = str(clear_log)
    env["CHANNEL_CLEAR_SLEEP"] = "0"
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        ["bash", str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_exit_zero(tmp_path: Path) -> None:
    result = _run_script(tmp_path)
    assert result.returncode == 0, result.stderr


def test_send_clear_key(tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    _run_script(tmp_path, call_log=log)
    calls = log.read_text()
    assert "send-keys" in calls
    assert "/clear" in calls


def test_send_enter_key(tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    _run_script(tmp_path, call_log=log)
    lines = [line for line in log.read_text().splitlines() if "send-keys" in line]
    assert any("Enter" in line for line in lines)


def test_send_clear_before_enter(tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    _run_script(tmp_path, call_log=log)
    lines = [line for line in log.read_text().splitlines() if "send-keys" in line]
    assert len(lines) >= 2, f"expected at least 2 send-keys calls, got: {lines}"
    clear_idx = next(i for i, line in enumerate(lines) if "/clear" in line)
    enter_idx = next(i for i, line in enumerate(lines) if "Enter" in line)
    assert clear_idx < enter_idx, "send-keys /clear must precede send-keys Enter"


def test_systemd_analyze_verify() -> None:
    service = _REPO_ROOT / "systemd" / "claude-soma-channel-clear.service"
    timer = _REPO_ROOT / "systemd" / "claude-soma-channel-clear.timer"
    try:
        result = subprocess.run(
            ["systemd-analyze", "verify", str(service), str(timer)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            deployment_errors = (
                "No such file or directory" in combined
                or "not executable" in combined
                or "EnvironmentFile" in combined
            )
            if deployment_errors:
                pytest.skip(
                    "systemd-analyze verify skipped: deployment paths absent "
                    f"(ExecStart or EnvironmentFile not installed under /opt); "
                    f"stderr: {result.stderr.strip()!r}"
                )
            assert False, (
                "systemd-analyze verify failed (unit syntax error):\n" + combined
            )
    except FileNotFoundError:
        pytest.skip("systemd-analyze not available in this environment")
