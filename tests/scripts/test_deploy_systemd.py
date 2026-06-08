"""Tests for scripts/deploy-systemd.sh.

Strategy: use tmp_path for fake SYSTEMD_REPO + SYSTEMD_DEST; pass a stub
systemctl via SYSTEMCTL_BIN that logs every call; set SUDO="" so no real sudo.
All assertions operate only on tmp dirs + the log file — nothing touches the
real /etc/systemd/system or any live service.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy-systemd.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_exe(path: Path, body: str) -> None:
    """Write a minimal shell stub and make it executable."""
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path]:
    """
    Set up:
      - fake SYSTEMD_REPO  (tmp_path/repo)
      - fake SYSTEMD_DEST  (tmp_path/dest)
      - stub systemctl     (tmp_path/bin/systemctl) that logs all args
      - SUDO=""            (no real privilege escalation)

    Returns (env_dict, repo_path, dest_path, systemctl_log_path).
    """
    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    repo.mkdir()
    dest.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "systemctl.log"

    # Stub systemctl: log every invocation; exit 0 for is-active so the
    # post-restart health check inside the script succeeds.
    stub_body = f"""
LOG="{log_file}"
echo "$@" >> "$LOG"
case "$1" in
    is-active) exit 0 ;;
    *) exit 0 ;;
esac
"""
    _write_exe(bin_dir / "systemctl", stub_body)

    env: dict[str, str] = {
        **os.environ,
        "SYSTEMD_REPO": str(repo),
        "SYSTEMD_DEST": str(dest),
        "SYSTEMCTL_BIN": str(bin_dir / "systemctl"),
        "SUDO": "",
    }
    return env, repo, dest, log_file


def _run(env: dict[str, str], args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(SCRIPT)] + (args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# 0. Syntax check
# ---------------------------------------------------------------------------

def test_script_syntax_is_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


# ---------------------------------------------------------------------------
# 1. Changed / new unit is copied to DEST
# ---------------------------------------------------------------------------

def test_new_unit_is_copied(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-healthcheck.timer").write_text("[Unit]\n[Timer]\nOnBootSec=1min\n[Install]\n")
    (dest / "claude-soma-healthcheck.service").write_text("[Unit]\n[Service]\nExecStart=/bin/true\n[Install]\n")

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
    assert (dest / "claude-soma-healthcheck.timer").exists(), "Timer was not copied to DEST"


def test_changed_unit_is_overwritten(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")
    (dest / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/old\n[Install]\n")

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
    assert (dest / "claude-soma-api.service").read_text() == "[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n"


# ---------------------------------------------------------------------------
# 2. daemon-reload is invoked after any change
# ---------------------------------------------------------------------------

def test_daemon_reload_called_on_change(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/true\n[Install]\n")

    _run(env)

    assert log_file.exists(), "systemctl was never called"
    log_content = log_file.read_text()
    assert "daemon-reload" in log_content, f"daemon-reload not found in systemctl log:\n{log_content}"


def test_daemon_reload_not_called_when_nothing_changed(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    content = "[Unit]\n[Service]\nExecStart=/bin/true\n[Install]\n"
    (repo / "claude-soma-api.service").write_text(content)
    (dest / "claude-soma-api.service").write_text(content)

    _run(env)

    if log_file.exists():
        assert "daemon-reload" not in log_file.read_text(), "daemon-reload should not be called when nothing changed"


# ---------------------------------------------------------------------------
# 3. Changed .timer (with sibling .service in DEST) is auto-restarted
# ---------------------------------------------------------------------------

def test_changed_timer_is_restarted_when_sibling_service_present(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-healthcheck.timer").write_text("[Unit]\n[Timer]\nOnBootSec=2min\n[Install]\n")
    # sibling .service must already exist in DEST
    (dest / "claude-soma-healthcheck.service").write_text("[Unit]\n[Service]\nExecStart=/bin/true\n[Install]\n")

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    log_content = log_file.read_text()
    assert "restart claude-soma-healthcheck.timer" in log_content, (
        f"Expected restart of timer in systemctl log:\n{log_content}"
    )


def test_changed_timer_not_restarted_without_sibling_service(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-healthcheck.timer").write_text("[Unit]\n[Timer]\nOnBootSec=2min\n[Install]\n")
    # No sibling .service in DEST

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    if log_file.exists():
        log_content = log_file.read_text()
        assert "restart claude-soma-healthcheck.timer" not in log_content, (
            "Timer should not be restarted without sibling .service in DEST"
        )
    assert "[SKIP]" in result.stdout or "sibling" in result.stdout


# ---------------------------------------------------------------------------
# 4. Changed .service NOT auto-restarted by default; RESTART REQUIRED printed
# ---------------------------------------------------------------------------

def test_changed_service_not_restarted_by_default(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")
    (dest / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/old\n[Install]\n")

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    assert "RESTART REQUIRED" in result.stdout, "Expected RESTART REQUIRED in output"

    if log_file.exists():
        log_content = log_file.read_text()
        assert "restart claude-soma-api.service" not in log_content, (
            "Service should not be auto-restarted without --restart-services"
        )


def test_changed_service_restarted_with_flag(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")
    (dest / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/old\n[Install]\n")

    result = _run(env, ["--restart-services"])
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    log_content = log_file.read_text()
    assert "restart claude-soma-api.service" in log_content, (
        f"Expected restart of api.service with --restart-services:\n{log_content}"
    )


# ---------------------------------------------------------------------------
# 5. claude-soma-channel.service NEVER restarted, even with --restart-services
# ---------------------------------------------------------------------------

def test_channel_service_never_restarted_default(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-channel.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")
    (dest / "claude-soma-channel.service").write_text("[Unit]\n[Service]\nExecStart=/bin/old\n[Install]\n")

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    if log_file.exists():
        log_content = log_file.read_text()
        assert "restart claude-soma-channel.service" not in log_content, (
            "channel.service must NEVER be auto-restarted"
        )
    assert "manual, never auto" in result.stdout, (
        "Expected 'manual, never auto' marker for channel service"
    )


def test_channel_service_never_restarted_with_restart_services_flag(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-channel.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")
    (dest / "claude-soma-channel.service").write_text("[Unit]\n[Service]\nExecStart=/bin/old\n[Install]\n")

    result = _run(env, ["--restart-services"])
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    if log_file.exists():
        log_content = log_file.read_text()
        assert "restart claude-soma-channel.service" not in log_content, (
            "channel.service must NEVER be auto-restarted, even with --restart-services"
        )
    assert "manual, never auto" in result.stdout


# ---------------------------------------------------------------------------
# 6. Unchanged unit is skipped (not copied, no restart)
# ---------------------------------------------------------------------------

def test_unchanged_unit_is_skipped(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    content = "[Unit]\n[Service]\nExecStart=/bin/true\n[Install]\n"
    (repo / "claude-soma-api.service").write_text(content)
    (dest / "claude-soma-api.service").write_text(content)

    dest_mtime_before = (dest / "claude-soma-api.service").stat().st_mtime

    result = _run(env)
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    dest_mtime_after = (dest / "claude-soma-api.service").stat().st_mtime
    assert dest_mtime_before == dest_mtime_after, "Unchanged unit should not be re-copied"
    assert "[SKIP]" in result.stdout, "Expected [SKIP] in output for unchanged unit"

    if log_file.exists():
        log_content = log_file.read_text()
        assert "restart claude-soma-api.service" not in log_content
        assert "daemon-reload" not in log_content


# ---------------------------------------------------------------------------
# 7. --dry-run changes nothing and invokes no systemctl mutations
# ---------------------------------------------------------------------------

def test_dry_run_copies_nothing(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")

    result = _run(env, ["--dry-run"])
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    assert not (dest / "claude-soma-api.service").exists(), (
        "No files should be copied during --dry-run"
    )
    assert "[DRY-RUN]" in result.stdout
    assert "DRY-RUN MODE" in result.stdout
    assert "DRY-RUN complete" in result.stdout


def test_dry_run_no_systemctl_mutations(tmp_path: Path) -> None:
    env, repo, dest, log_file = _make_env(tmp_path)
    (repo / "claude-soma-healthcheck.timer").write_text("[Unit]\n[Timer]\nOnBootSec=1min\n[Install]\n")
    (dest / "claude-soma-healthcheck.service").write_text("[Unit]\n[Service]\nExecStart=/bin/true\n[Install]\n")
    (repo / "claude-soma-api.service").write_text("[Unit]\n[Service]\nExecStart=/bin/new\n[Install]\n")

    _run(env, ["--dry-run"])

    # The stub systemctl must NOT have been called at all (no mutations under dry-run)
    assert not log_file.exists() or log_file.read_text().strip() == "", (
        f"systemctl should not be invoked during --dry-run; log:\n{log_file.read_text() if log_file.exists() else ''}"
    )
