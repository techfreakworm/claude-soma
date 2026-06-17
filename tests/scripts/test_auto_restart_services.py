"""Tests for scripts/auto-restart-services.sh.

All tests use a fake 'sudo' + fake 'systemctl' injected at the front of PATH
so that no real services are touched. The fake binaries record calls to a
temp file that the tests inspect.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "auto-restart-services.sh"


def _make_fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    """Write a fake executable and return its path."""
    p = tmp_path / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _run_script(
    tmp_path: Path,
    services_arg: str,
    *,
    window_offset_secs: int = 300,
    env_extra: dict[str, str] | None = None,
    call_log: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run auto-restart-services.sh with a mocked sudo+systemctl.

    window_offset_secs: seconds added to now for the expiry window
                        (positive = window still open, negative = expired).
    """
    log = call_log or (tmp_path / "systemctl-calls.log")

    # fake systemctl logs its args
    _make_fake_bin(
        tmp_path,
        "systemctl",
        f'echo "systemctl $*" >> "{log}"',
    )
    # fake sudo just execs its arguments
    _make_fake_bin(
        tmp_path,
        "sudo",
        "exec \"$@\"",
    )

    window = str(int(time.time()) + window_offset_secs)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["HERMES_AUTO_RESTART_WINDOW_UTC"] = window
    env["SOMA_AUTO_RESTART_LOCKFILE"] = str(tmp_path / "auto-restart.lock")
    # Keep the post-restart operator notify (soma_notify) off the network in tests.
    env["SOMA_NOTIFY_DISCORD_DISABLED"] = "1"
    env["SOMA_NOTIFY_TELEGRAM_DISABLED"] = "1"
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        ["bash", str(_SCRIPT), services_arg],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


# ------------------------------------------------------------------ tests

def test_valid_service_is_restarted(tmp_path: Path) -> None:
    """A valid claude-soma-*.service name triggers sudo systemctl restart."""
    log = tmp_path / "calls.log"
    result = _run_script(
        tmp_path,
        "claude-soma-channel.service",
        call_log=log,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "restart claude-soma-channel.service" in calls


def test_multiple_services_all_restarted(tmp_path: Path) -> None:
    """Comma-separated services are each restarted in order."""
    log = tmp_path / "calls.log"
    result = _run_script(
        tmp_path,
        "claude-soma-api.service,claude-soma-channel.service",
        call_log=log,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "restart claude-soma-api.service" in calls
    assert "restart claude-soma-channel.service" in calls


def test_expired_window_skips_restart(tmp_path: Path) -> None:
    """When the window timestamp is in the past, no restart occurs."""
    log = tmp_path / "calls.log"
    result = _run_script(
        tmp_path,
        "claude-soma-channel.service",
        window_offset_secs=-60,  # expired 60s ago
        call_log=log,
    )
    assert result.returncode == 0
    assert not log.exists() or "restart" not in log.read_text()


def test_no_window_env_var_skips_restart(tmp_path: Path) -> None:
    """When HERMES_AUTO_RESTART_WINDOW_UTC is not set, no restart occurs."""
    log = tmp_path / "calls.log"
    _make_fake_bin(tmp_path, "systemctl", f'echo "systemctl $*" >> "{log}"')
    _make_fake_bin(tmp_path, "sudo", 'exec "$@"')

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["SOMA_AUTO_RESTART_LOCKFILE"] = str(tmp_path / "auto-restart.lock")
    env.pop("HERMES_AUTO_RESTART_WINDOW_UTC", None)

    result = subprocess.run(
        ["bash", str(_SCRIPT), "claude-soma-channel.service"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert not log.exists() or "restart" not in log.read_text()


def test_invalid_service_name_is_skipped(tmp_path: Path) -> None:
    """Service names not matching ^claude-soma-[a-z][a-z0-9-]*.service$ are skipped."""
    log = tmp_path / "calls.log"
    # inject ; rm -rf / style injection attempt — should be rejected
    result = _run_script(
        tmp_path,
        "evil-service.service",
        call_log=log,
    )
    assert result.returncode == 0
    assert not log.exists() or "restart evil-service" not in log.read_text()


def test_mixed_valid_invalid_only_valid_restarted(tmp_path: Path) -> None:
    """Valid service is restarted; invalid service in same list is skipped."""
    log = tmp_path / "calls.log"
    result = _run_script(
        tmp_path,
        "claude-soma-api.service,bad-name,claude-soma-channel.service",
        call_log=log,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "restart claude-soma-api.service" in calls
    assert "restart claude-soma-channel.service" in calls
    assert "restart bad-name" not in calls


def test_first_invocation_creates_window_marker(tmp_path: Path) -> None:
    """First invocation restarts services and creates the once-per-window marker."""
    window = str(int(time.time()) + 3600)
    lockfile = tmp_path / "auto-restart.lock"
    marker = Path(f"{lockfile}.fired-{window}")
    log = tmp_path / "calls.log"
    result = _run_script(
        tmp_path,
        "claude-soma-channel.service",
        call_log=log,
        env_extra={"HERMES_AUTO_RESTART_WINDOW_UTC": window},
    )
    assert result.returncode == 0, result.stderr
    assert marker.exists(), f"Expected marker {marker} to be created"
    calls = log.read_text()
    assert "restart claude-soma-channel.service" in calls


def test_second_invocation_same_window_is_skipped(tmp_path: Path) -> None:
    """Second invocation within the same window exits 0 without calling systemctl."""
    window = str(int(time.time()) + 3600)
    lockfile = tmp_path / "auto-restart.lock"
    marker = Path(f"{lockfile}.fired-{window}")
    log = tmp_path / "calls.log"
    marker.touch()
    result = _run_script(
        tmp_path,
        "claude-soma-channel.service",
        call_log=log,
        env_extra={"HERMES_AUTO_RESTART_WINDOW_UTC": window},
    )
    assert result.returncode == 0
    assert "already fired this window" in result.stdout
    assert not log.exists() or "restart" not in log.read_text()


def test_empty_services_arg_exits_nonzero(tmp_path: Path) -> None:
    """Calling the script with no services argument exits with code 1."""
    log = tmp_path / "calls.log"
    _make_fake_bin(tmp_path, "systemctl", f'echo "systemctl $*" >> "{log}"')
    _make_fake_bin(tmp_path, "sudo", 'exec "$@"')

    window = str(int(time.time()) + 300)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["HERMES_AUTO_RESTART_WINDOW_UTC"] = window
    env["SOMA_AUTO_RESTART_LOCKFILE"] = str(tmp_path / "auto-restart.lock")

    result = subprocess.run(
        ["bash", str(_SCRIPT), ""],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
