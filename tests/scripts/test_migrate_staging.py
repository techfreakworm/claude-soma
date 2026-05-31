"""Tests for scripts/migrate-staging.sh (idempotent migration helper).

Each test invokes the script via subprocess with env vars redirecting SRC/DEST
to tmp_path, so no live filesystem paths are touched.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate-staging.sh"


def _run(src: Path, dest: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "SOMA_STAGING_SRC": str(src),
        "SOMA_STAGING_DEST": str(dest),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_happy_path_moves_files(tmp_path: Path) -> None:
    src = tmp_path / "social-engagement"
    dest = tmp_path / "staging"
    src.mkdir()
    (src / "BUGS_PLAN.md").write_text("bug plan")
    (src / "demo.sh").write_text("#!/bin/bash\necho hi")

    result = _run(src, dest)

    assert result.returncode == 0, result.stderr
    assert not src.exists(), "source dir should be removed after migration"
    assert (dest / "BUGS_PLAN.md").exists()
    assert (dest / "demo.sh").exists()
    assert "moved 2 item(s)" in result.stdout


def test_idempotent_second_run_reports_nothing(tmp_path: Path) -> None:
    src = tmp_path / "social-engagement"
    dest = tmp_path / "staging"
    src.mkdir()
    (src / "PLAN.md").write_text("plan")

    # First run
    result1 = _run(src, dest)
    assert result1.returncode == 0, result1.stderr
    assert "moved" in result1.stdout

    # Second run: source is gone
    result2 = _run(src, dest)
    assert result2.returncode == 0, result2.stderr
    assert "nothing to migrate" in result2.stdout


def test_symlinks_preserved(tmp_path: Path) -> None:
    src = tmp_path / "social-engagement"
    dest = tmp_path / "staging"
    src.mkdir()

    target = tmp_path / "real-file.md"
    target.write_text("real content")
    link = src / "linked-doc.md"
    link.symlink_to(target)

    result = _run(src, dest)
    assert result.returncode == 0, result.stderr

    migrated_link = dest / "linked-doc.md"
    assert migrated_link.exists() or migrated_link.is_symlink(), "link should exist in dest"
    assert migrated_link.is_symlink(), "migrated item should remain a symlink, not a copy"


def test_missing_source_exits_zero_with_message(tmp_path: Path) -> None:
    src = tmp_path / "nonexistent-src"
    dest = tmp_path / "staging"

    result = _run(src, dest)
    assert result.returncode == 0, result.stderr
    assert "nothing to migrate" in result.stdout


def test_empty_source_dir_exits_zero_with_message(tmp_path: Path) -> None:
    src = tmp_path / "social-engagement"
    dest = tmp_path / "staging"
    src.mkdir()  # exists but empty

    result = _run(src, dest)
    assert result.returncode == 0, result.stderr
    assert "nothing to migrate" in result.stdout


def test_dest_created_if_absent(tmp_path: Path) -> None:
    src = tmp_path / "social-engagement"
    dest = tmp_path / "staging"
    src.mkdir()
    (src / "file.md").write_text("content")

    assert not dest.exists(), "dest should not exist before migration"
    result = _run(src, dest)

    assert result.returncode == 0, result.stderr
    assert dest.is_dir(), "dest should be created by the script"


def test_dest_permissions_0755(tmp_path: Path) -> None:
    src = tmp_path / "social-engagement"
    dest = tmp_path / "staging"
    src.mkdir()
    (src / "file.md").write_text("content")

    result = _run(src, dest)
    assert result.returncode == 0, result.stderr

    mode = dest.stat().st_mode & 0o777
    assert mode == 0o755, f"expected 0755, got {oct(mode)}"
