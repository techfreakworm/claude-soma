"""Tests for scripts/relay_cleanup.sh (bash cleanup script).

Each test invokes the script via subprocess with env vars pointing to a tmpdir,
so no live filesystem paths are touched.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "relay_cleanup.sh"


def _run(relay_root: Path, log_path: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HERMES_RELAY_ROOT": str(relay_root),
        "HERMES_RELAY_CLEANUP_LOG": str(log_path),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def _make_old_file(path: Path, days: int = 10) -> None:
    """Create a file and backdate its mtime to `days` days ago."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old content")
    old_time = time.time() - (days * 86400)
    os.utime(path, (old_time, old_time))


def _make_new_file(path: Path) -> None:
    """Create a file with a current mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("new content")


def test_happy_path_old_file_deleted(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    log = tmp_path / "cleanup.log"

    old_file = relay_root / "test-lead" / "stale.pptx"
    _make_old_file(old_file, days=10)

    result = _run(relay_root, log, extra_env={"HERMES_RELAY_RETENTION_DAYS": "7"})
    assert result.returncode == 0, result.stderr
    assert not old_file.exists()
    assert "deleted=1" in result.stdout


def test_pinned_dir_preserves_files(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    log = tmp_path / "cleanup.log"

    pinned_dir = relay_root / "pinned-lead"
    pinned_dir.mkdir(parents=True)
    pin_marker = pinned_dir / ".pin"
    pin_marker.write_text("")

    old_file = pinned_dir / "keep-me.mp4"
    _make_old_file(old_file, days=30)

    result = _run(relay_root, log, extra_env={"HERMES_RELAY_RETENTION_DAYS": "7"})
    assert result.returncode == 0, result.stderr
    assert old_file.exists(), "Pinned file should not be deleted"
    assert "skipped_pinned=1" in result.stdout


def test_recent_file_not_deleted(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    log = tmp_path / "cleanup.log"

    new_file = relay_root / "active-lead" / "fresh.png"
    _make_new_file(new_file)

    result = _run(relay_root, log, extra_env={"HERMES_RELAY_RETENTION_DAYS": "7"})
    assert result.returncode == 0, result.stderr
    assert new_file.exists(), "Recent file should not be deleted"
    assert "deleted=0" in result.stdout


def test_env_override_relay_root(tmp_path: Path) -> None:
    default_root = tmp_path / "wrong"
    custom_root = tmp_path / "correct"
    log = tmp_path / "cleanup.log"

    old_file = custom_root / "lead" / "old.txt"
    _make_old_file(old_file, days=10)

    # HERMES_RELAY_ROOT set to custom_root via extra_env override
    result = _run(custom_root, log, extra_env={"HERMES_RELAY_RETENTION_DAYS": "7"})
    assert result.returncode == 0, result.stderr
    assert not old_file.exists()
    # default_root should not have been touched (or created)
    assert not default_root.exists()


def test_summary_line_emitted(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    log = tmp_path / "cleanup.log"
    relay_root.mkdir()

    result = _run(relay_root, log)
    assert result.returncode == 0, result.stderr
    assert "relay-cleanup:" in result.stdout
    assert "deleted=" in result.stdout
    assert "skipped_pinned=" in result.stdout


def test_missing_root_exits_zero_with_message(tmp_path: Path) -> None:
    relay_root = tmp_path / "nonexistent"
    log = tmp_path / "cleanup.log"

    result = _run(relay_root, log)
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout


def test_log_file_records_deletions(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    log = tmp_path / "cleanup.log"

    old_file = relay_root / "lead1" / "report.pdf"
    _make_old_file(old_file, days=10)

    result = _run(relay_root, log, extra_env={"HERMES_RELAY_RETENTION_DAYS": "7"})
    assert result.returncode == 0, result.stderr
    assert log.exists()
    log_content = log.read_text()
    assert '"event":"deleted"' in log_content
    assert "report.pdf" in log_content
