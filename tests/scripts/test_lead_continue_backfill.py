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

ENV_NAME_TEMPLATE = 'Environment="HERMES_LEAD_NAME={name}"'
ENV_ENDPOINT_LINE = 'Environment="HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100"'


def _make_unit(
    tmp_path: Path,
    name: str,
    *,
    already_patched: bool = False,
    env_preinjected: bool = False,
) -> Path:
    """Write a synthetic transient unit file and return its path."""
    content = UNIT_TEMPLATE.format(name=name, claude_bin=CLAUDE_BIN)
    if already_patched:
        content = content.replace(
            f'"{CLAUDE_BIN}" "--remote-control"',
            f'"{CLAUDE_BIN}" "--continue" "--remote-control"',
        )
    if env_preinjected:
        # Insert env lines immediately after the [Service] heading.
        content = content.replace(
            "[Service]\n",
            "[Service]\n"
            + ENV_NAME_TEMPLATE.format(name=name) + "\n"
            + ENV_ENDPOINT_LINE + "\n",
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

    # Second run: unit already has "--continue" AND env vars; both skipped.
    second = _run_script(tmp_path)
    assert second.returncode == 0
    assert "skipped=1" in second.stdout
    assert "patched=0" in second.stdout
    assert "errored=0" in second.stdout
    assert "env_skipped=1" in second.stdout
    assert "env_patched=0" in second.stdout


def test_no_match_pattern_warns_and_does_not_patch(tmp_path: Path) -> None:
    """A unit file that doesn't have the expected binary token pair is warned
    about and left unmodified (env injection is also skipped for that unit)."""
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
    # File is unchanged — env injection is skipped when binary pattern missing.
    assert unit_path.read_text() == original


# ---------------------------------------------------------------------------
# FI-ENV-BACKFILL new tests
# ---------------------------------------------------------------------------


def test_env_injection_adds_both_lines(tmp_path: Path) -> None:
    """A unit without FI-NOTIFY env vars gets both Environment= lines injected."""
    unit = _make_unit(tmp_path, "soma-improver")

    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    content = unit.read_text()
    assert ENV_NAME_TEMPLATE.format(name="soma-improver") in content, (
        f"Missing HERMES_LEAD_NAME in:\n{content}"
    )
    assert ENV_ENDPOINT_LINE in content, (
        f"Missing HERMES_NOTIFY_ENDPOINT in:\n{content}"
    )
    assert "env_patched=1" in result.stdout


def test_env_injection_idempotent(tmp_path: Path) -> None:
    """A unit that already has both env lines is skipped; env_skipped increments."""
    _make_unit(tmp_path, "soma-improver", already_patched=True, env_preinjected=True)

    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "env_skipped=1" in result.stdout
    assert "env_patched=0" in result.stdout


def test_env_injection_correct_name_extraction(tmp_path: Path) -> None:
    """Name extracted from filename claude-soma-lead-mayank-portfolio.service
    equals 'mayank-portfolio', not the full basename."""
    unit = _make_unit(tmp_path, "mayank-portfolio")

    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    content = unit.read_text()
    assert 'Environment="HERMES_LEAD_NAME=mayank-portfolio"' in content, (
        f"Wrong or missing HERMES_LEAD_NAME in:\n{content}"
    )
    # Ensure the full unit-filename fragment is NOT used as the name.
    assert "claude-soma-lead-mayank-portfolio" not in content.split("HERMES_LEAD_NAME=")[1].split("\n")[0]


def test_continue_and_env_both_applied_to_same_unit(tmp_path: Path) -> None:
    """A unit missing both --continue and env vars gets both injected in one run."""
    unit = _make_unit(tmp_path, "soma-improver")

    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    content = unit.read_text()
    assert UNIT_PATCHED_MARKER in content, "--continue not injected"
    assert ENV_NAME_TEMPLATE.format(name="soma-improver") in content, "HERMES_LEAD_NAME not injected"
    assert ENV_ENDPOINT_LINE in content, "HERMES_NOTIFY_ENDPOINT not injected"
    assert "patched=1" in result.stdout
    assert "env_patched=1" in result.stdout
    assert "skipped=0" in result.stdout
    assert "env_skipped=0" in result.stdout


def test_summary_line_format(tmp_path: Path) -> None:
    """Summary line contains all parseable counter fields."""
    _make_unit(tmp_path, "soma-improver")

    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    # All expected key=value fields are present in the summary.
    summary = next(
        (line for line in result.stdout.splitlines() if line.startswith("backfill summary:")),
        None,
    )
    assert summary is not None, f"No 'backfill summary:' line in output:\n{result.stdout}"
    for field in ("patched=", "env_patched=", "skipped=", "env_skipped=", "errored="):
        assert field in summary, f"Field {field!r} missing from summary line: {summary!r}"
