# tests/scripts/test_lead_continue_backfill.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lead_continue_backfill.sh"

CLAUDE_BIN = "/home/ubuntu/.local/bin/claude"

# A realistic synthetic transient unit file that mirrors the ExecStart shape
# produced by _wrap_in_transient_unit + spawn_background_lead.
UNIT_TEMPLATE = """\
# This is a transient unit file, created programmatically via the systemd API. Do not edit.
[Unit]
Description=claude soma lead {name}

[Service]
Type=oneshot
RemainAfterExit=yes
User=ubuntu
Group=ubuntu
ExecStart=
ExecStart="/usr/bin/tmux" "-L" "soma-lead-{name}" "new-session" "-d" "-s" "soma-proj-{name}" \
"-c" "/home/ubuntu/projects/{name}" \
"{claude_bin}" "--remote-control" "soma-proj-{name}" \
"--add-dir" "/home/ubuntu/projects/{name}" \
"--permission-mode" "acceptEdits" \
"--dangerously-skip-permissions" "--effort" "max" \
"--setting-sources" "user,project,local" "--" "brief text here"
"""

UNIT_PATCHED_MARKER = f'"{CLAUDE_BIN}" "--continue" "--remote-control"'


def _make_unit(tmp_path: Path, name: str, *, already_patched: bool = False) -> Path:
    """Write a synthetic transient unit file and return its path."""
    content = UNIT_TEMPLATE.format(name=name, claude_bin=CLAUDE_BIN)
    if already_patched:
        content = content.replace(
            f'"{CLAUDE_BIN}" "--remote-control"',
            f'"{CLAUDE_BIN}" "--continue" "--remote-control"',
        )
    unit_path = tmp_path / f"claude-soma-lead-{name}.service"
    unit_path.write_text(content)
    return unit_path


def _run_script(tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LEAD_CONTINUE_BACKFILL_DIR": str(tmp_path),
        "LEAD_CONTINUE_BACKFILL_NOSUDO": "1",
    }
    return subprocess.run(
        [str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_happy_path_patches_unit(tmp_path: Path) -> None:
    """A unit with the expected binary+flag token pair gets --continue inserted."""
    unit = _make_unit(tmp_path, "soma-improver")

    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    # Summary line reflects one patch.
    assert "patched=1" in result.stdout
    assert "skipped=0" in result.stdout
    assert "errored=0" in result.stdout
    # The unit file now has "--continue" between the binary and --remote-control.
    content = unit.read_text()
    assert UNIT_PATCHED_MARKER in content, (
        f"Expected {UNIT_PATCHED_MARKER!r} in unit after patching.\n"
        f"Unit content:\n{content}\nScript output:\n{result.stdout}"
    )


def test_idempotent_skips_already_patched_unit(tmp_path: Path) -> None:
    """Running the script twice: second run skips the already-patched unit."""
    _make_unit(tmp_path, "soma-improver")

    # First run: patches the unit.
    first = _run_script(tmp_path)
    assert first.returncode == 0
    assert "patched=1" in first.stdout

    # Second run: unit already has "--continue", must be skipped.
    second = _run_script(tmp_path)
    assert second.returncode == 0
    assert "skipped=1" in second.stdout
    assert "patched=0" in second.stdout
    assert "errored=0" in second.stdout


def test_no_match_pattern_warns_and_does_not_patch(tmp_path: Path) -> None:
    """A unit file that doesn't have the expected binary token pair is warned
    about and left unmodified."""
    # Write a unit with a different (non-matching) binary path so the sed
    # pattern won't find it.
    content = UNIT_TEMPLATE.format(name="mystery", claude_bin="/usr/bin/claude-other")
    unit_path = tmp_path / "claude-soma-lead-mystery.service"
    unit_path.write_text(content)
    original = unit_path.read_text()

    result = _run_script(tmp_path)

    assert result.returncode == 0
    # Script warns about the unrecognised pattern.
    assert "warn:" in result.stdout
    assert "errored=1" in result.stdout
    assert "patched=0" in result.stdout
    # File is unchanged.
    assert unit_path.read_text() == original
