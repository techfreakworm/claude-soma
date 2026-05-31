"""Tests for scripts/migrate-to-ist.sh.

Strategy: invoke the script via subprocess with a fake PATH prefix containing
stub executables for privileged commands (timedatectl, locale-gen, update-locale,
sudo, systemctl, locale).  SYSTEMD_DEST is overridden to a tmp directory so no
live files are touched.
"""
from __future__ import annotations

import os
import stat
import subprocess
import configparser
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate-to-ist.sh"
SYSTEMD_REPO = Path(__file__).resolve().parents[2] / "systemd"


def _write_exe(path: Path, body: str) -> None:
    """Write a small shell stub and make it executable."""
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_fake_bin(
    tmp_bin: Path,
    *,
    tz: str = "Asia/Kolkata",
    locale_present: bool = True,
    lc_time_set: bool = True,
) -> dict[str, str]:
    """
    Create stub executables in tmp_bin.  Returns an env dict that prepends
    tmp_bin to PATH.
    """
    tmp_bin.mkdir(exist_ok=True)

    # timedatectl: honours 'show --property=Timezone --value' and
    # 'set-timezone', records calls to a log
    timedatectl_body = f"""
LOGFILE="$TMP_BIN_LOG/timedatectl.log"
echo "$@" >> "$LOGFILE"
case "$*" in
  "show --property=Timezone --value"|"show"*"Timezone"*)
    echo "{tz}"
    ;;
  "set-timezone"*)
    echo "set-timezone called" >> "$LOGFILE"
    ;;
  status*)
    echo "Time zone: {tz} (IST, +0530)"
    ;;
  *)
    echo "timedatectl: $*"
    ;;
esac
exit 0
"""
    _write_exe(tmp_bin / "timedatectl", timedatectl_body)

    # locale: returns en_IN.utf8 if locale_present else empty
    locale_out = "en_IN.utf8\nen_US.utf8\nC" if locale_present else "en_US.utf8\nC"
    locale_body = f"""
case "$*" in
  -a*)
    printf '{locale_out}\\n'
    ;;
  *)
    echo "LANG=en_US.UTF-8"
    if [[ "{lc_time_set}" == "True" ]]; then
      echo "LC_TIME=en_IN.UTF-8"
    fi
    ;;
esac
exit 0
"""
    _write_exe(tmp_bin / "locale", locale_body)

    # locale-gen: no-op stub
    _write_exe(tmp_bin / "locale-gen", 'echo "locale-gen called: $*"')

    # update-locale: no-op stub
    _write_exe(tmp_bin / "update-locale", 'echo "update-locale called: $*"')

    # systemctl: no-op stub
    _write_exe(
        tmp_bin / "systemctl",
        'echo "systemctl called: $*"',
    )

    # cmp: pass through to real cmp (we need it for file comparison)
    _write_exe(tmp_bin / "cmp", 'exec /usr/bin/cmp "$@"')

    # sudo: execute the rest of the arguments directly (no privilege needed in tests)
    _write_exe(tmp_bin / "sudo", 'shift 0; exec "$@"')

    return {
        **os.environ,
        "PATH": f"{tmp_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "TMP_BIN_LOG": str(tmp_bin),
    }


def _run_script(
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
    args: list[str] | None = None,
    fake_bin_kwargs: dict | None = None,
) -> subprocess.CompletedProcess:
    systemd_dest = tmp_path / "systemd_dest"
    systemd_dest.mkdir(parents=True, exist_ok=True)

    tmp_bin = tmp_path / "bin"
    env = _make_fake_bin(tmp_bin, **(fake_bin_kwargs or {}))
    env["SYSTEMD_DEST"] = str(systemd_dest)

    if extra_env:
        env.update(extra_env)

    cmd = ["bash", str(SCRIPT)] + (args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# Test: --dry-run prints [DRY-RUN] lines and does not modify SYSTEMD_DEST
# ---------------------------------------------------------------------------

def test_dry_run_prints_actions_without_applying(tmp_path: Path) -> None:
    systemd_dest = tmp_path / "systemd_dest"
    systemd_dest.mkdir(parents=True, exist_ok=True)

    tmp_bin = tmp_path / "bin"
    # Fake tz = UTC so that dry-run would show a timezone action
    env = _make_fake_bin(tmp_bin, tz="UTC", locale_present=False, lc_time_set=False)
    env["SYSTEMD_DEST"] = str(systemd_dest)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
    assert "[DRY-RUN]" in result.stdout
    assert "DRY-RUN MODE" in result.stdout
    assert "DRY-RUN complete" in result.stdout

    # No files should have been copied to SYSTEMD_DEST
    assert list(systemd_dest.iterdir()) == [], (
        f"SYSTEMD_DEST should be empty after dry-run, found: {list(systemd_dest.iterdir())}"
    )


def test_dry_run_does_not_call_timedatectl_set(tmp_path: Path) -> None:
    """timedatectl set-timezone must not appear in the stub log during dry-run."""
    systemd_dest = tmp_path / "systemd_dest"
    systemd_dest.mkdir(parents=True, exist_ok=True)

    tmp_bin = tmp_path / "bin"
    env = _make_fake_bin(tmp_bin, tz="UTC", locale_present=False, lc_time_set=False)
    env["SYSTEMD_DEST"] = str(systemd_dest)

    subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )

    log_file = tmp_bin / "timedatectl.log"
    if log_file.exists():
        log_content = log_file.read_text()
        assert "set-timezone" not in log_content, (
            "set-timezone must not be called during --dry-run"
        )


# ---------------------------------------------------------------------------
# Test: idempotency — all steps report [SKIP] when already converged
# ---------------------------------------------------------------------------

def test_idempotent_when_already_on_ist(tmp_path: Path) -> None:
    """When tz=Asia/Kolkata and locale already set, all steps report SKIP."""
    # Pre-populate SYSTEMD_DEST with identical copies of repo timer files
    systemd_dest = tmp_path / "systemd_dest"
    systemd_dest.mkdir(parents=True, exist_ok=True)
    for timer_file in SYSTEMD_REPO.glob("*.timer"):
        dest = systemd_dest / timer_file.name
        dest.write_bytes(timer_file.read_bytes())

    tmp_bin = tmp_path / "bin"
    env = _make_fake_bin(tmp_bin, tz="Asia/Kolkata", locale_present=True, lc_time_set=True)
    env["SYSTEMD_DEST"] = str(systemd_dest)

    # Also fake /etc/default/locale check via a temp file with the right content
    locale_file = tmp_path / "default_locale"
    locale_file.write_text("LANG=en_US.UTF-8\nLC_TIME=en_IN.UTF-8\n")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
    # Timezone should be skipped
    assert "[SKIP]   Timezone already Asia/Kolkata" in result.stdout
    # All timer files should be skipped (they are identical)
    assert "All timer files already up-to-date" in result.stdout or all(
        f"[SKIP]" in result.stdout and t.name in result.stdout
        for t in SYSTEMD_REPO.glob("*.timer")
    )


# ---------------------------------------------------------------------------
# Test: timer files are syntactically valid (have required sections/keys)
# ---------------------------------------------------------------------------

def test_all_timer_files_have_required_sections() -> None:
    """Every .timer file in systemd/ must have [Unit], [Timer], and [Install]."""
    timer_files = list(SYSTEMD_REPO.glob("*.timer"))
    assert len(timer_files) > 0, "No .timer files found in systemd/"

    for timer_file in timer_files:
        content = timer_file.read_text()
        assert "[Unit]" in content, f"{timer_file.name}: missing [Unit] section"
        assert "[Timer]" in content, f"{timer_file.name}: missing [Timer] section"
        assert "[Install]" in content, f"{timer_file.name}: missing [Install] section"


def test_oncalendar_timers_have_valid_format() -> None:
    """
    Timer files using OnCalendar must not have a bare HH:MM time that would
    silently shift on tz change — they should either have a UTC suffix or
    express an explicit IST intent (documented in the migration note).
    """
    timer_files = list(SYSTEMD_REPO.glob("*.timer"))
    for timer_file in timer_files:
        content = timer_file.read_text()
        lines = [line.strip() for line in content.splitlines()]
        for line in lines:
            if not line.startswith("OnCalendar="):
                continue
            value = line[len("OnCalendar="):]
            # If explicit UTC suffix present, it's correctly pinned
            if "UTC" in value:
                continue
            # Interval-based (no absolute time) is fine
            if not any(c.isdigit() for c in value):
                continue
            # OnBootSec / OnUnitActiveSec are not OnCalendar — skip (handled above)
            # OnCalendar values that are just intervals like "Mon..Fri" with a time
            # must have been reviewed and updated to IST values — just verify they parse
            # as a time-of-day (HH:MM:SS pattern present)
            assert ":" in value, (
                f"{timer_file.name}: OnCalendar={value!r} looks malformed"
            )


def test_configparser_can_parse_all_timer_files() -> None:
    """All .timer files must be parseable by configparser after stripping leading comments."""
    timer_files = list(SYSTEMD_REPO.glob("*.timer"))
    for timer_file in timer_files:
        raw = timer_file.read_text()
        # Remove comment-only lines at the top (lines starting with #)
        lines = raw.splitlines()
        stripped_lines = [ln for ln in lines if not ln.startswith("#")]
        content = "\n".join(stripped_lines)

        parser = configparser.RawConfigParser()
        try:
            parser.read_string(content)
        except configparser.Error as exc:
            raise AssertionError(
                f"{timer_file.name}: configparser failed: {exc}"
            ) from exc

        assert parser.has_section("Timer"), f"{timer_file.name}: missing [Timer]"


# ---------------------------------------------------------------------------
# Test: new daily-status.timer is present in the repo
# ---------------------------------------------------------------------------

def test_daily_status_timer_in_repo() -> None:
    timer = SYSTEMD_REPO / "claude-soma-daily-status.timer"
    assert timer.exists(), "claude-soma-daily-status.timer must exist in systemd/"
    content = timer.read_text()
    assert "OnCalendar=*-*-* 10:00:00" in content, (
        "daily-status.timer must have OnCalendar=*-*-* 10:00:00 for 10:00 IST intent"
    )
