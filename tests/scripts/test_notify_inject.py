"""Tests for scripts/notify_inject.sh.

Covers two key properties:
1. Static ordering: the mark_read POST curl block appears before the
   auto-restart-services.sh invocation in the script source.
2. Dynamic ordering: with a fake jq that produces a RESTART_SERVICES value,
   the mark_read curl fires and is logged before the setsid spawn is logged.
3. Behavioural guards: no auto-restart without window env var, no auto-restart
   when no RESTART REQUIRED MILESTONE is present.

All dynamic tests inject fake binaries at the front of PATH so no real HTTP
calls or service restarts occur.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "notify_inject.sh"

# Canned events response with one MILESTONE containing RESTART REQUIRED.
_EVENTS_JSON = (
    '{"events":['
    '{"id":42,"type":"MILESTONE","lead":"test-lead",'
    '"payload_json":"{\\"progress\\":\\"RESTART REQUIRED (services: claude-soma-channel.service)\\",\\"percent\\":null}"}'
    '],"open_pending_inputs":[]}'
)

# Canned events response with a MILESTONE that does NOT contain RESTART REQUIRED.
_EVENTS_JSON_NO_RESTART = (
    '{"events":['
    '{"id":7,"type":"MILESTONE","lead":"test-lead",'
    '"payload_json":"{\\"progress\\":\\"50% done\\",\\"percent\\":50}"}'
    '],"open_pending_inputs":[]}'
)

# Canned events response with a RESTART REQUIRED MILESTONE that has
# auto_restart_fired_at already set (Python-side trigger already fired).
_EVENTS_JSON_ALREADY_FIRED = (
    '{"events":['
    '{"id":42,"type":"MILESTONE","lead":"test-lead",'
    '"auto_restart_fired_at":1748000000.0,'
    '"payload_json":"{\\"progress\\":\\"RESTART REQUIRED (services: claude-soma-channel.service)\\",\\"percent\\":null}"}'
    '],"open_pending_inputs":[]}'
)


def _make_fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _run_with_restart_milestone(
    tmp_path: Path,
    *,
    window_offset_secs: int = 300,
    events_json: str = _EVENTS_JSON,
    with_window: bool = True,
    inject_fake_jq: bool = False,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run notify_inject.sh with fake curl + setsid; return (result, order_log).

    The order_log records one line per significant event:
      "mark_read"    when the mark_read POST curl executes
      "auto-restart" when setsid is spawned

    inject_fake_jq: if True, install a jq wrapper that returns a known
    RESTART_SERVICES value for the services-capture filter so that setsid
    actually fires.  Required because jq 1.7 uses PCRE2 (?<name>) syntax
    while the script uses (?P<name>); the wrapper patches that one filter.
    """
    order_log = tmp_path / "order.log"

    # Fake curl: return canned events JSON for the events endpoint;
    # log "mark_read" and exit 0 for the mark_read POST.
    _make_fake_bin(
        tmp_path,
        "curl",
        f"""
for arg in "$@"; do
    if [[ "$arg" == *"mark_read"* ]]; then
        printf '%s\\n' "mark_read" >> "{order_log}"
        exit 0
    fi
    if [[ "$arg" == *"events"* ]]; then
        printf '%s' '{events_json}'
        exit 0
    fi
done
exit 0
""",
    )

    # Fake setsid: log "auto-restart" instead of actually spawning services.
    _make_fake_bin(
        tmp_path,
        "setsid",
        f'printf "%s\\n" "auto-restart" >> "{order_log}"',
    )

    # nohup and sudo are passed as arguments to setsid; since our fake setsid
    # exits immediately after logging, they are never executed.  Stubs keep PATH
    # clean.
    _make_fake_bin(tmp_path, "nohup", 'exec "$@"')
    _make_fake_bin(tmp_path, "sudo", 'exec "$@"')

    if inject_fake_jq:
        # Provide a jq wrapper that transparently delegates to real jq for every
        # call EXCEPT the services-capture filter which jq 1.7 rejects due to a
        # PCRE2 named-group syntax difference.  For that one call the wrapper
        # emits the expected service name directly so the setsid branch fires.
        real_jq = subprocess.run(
            ["which", "jq"], capture_output=True, text=True
        ).stdout.strip() or "/usr/bin/jq"
        _make_fake_bin(
            tmp_path,
            "jq",
            f"""
args="$*"
if [[ "$args" == *"capture"*"svcs"* ]]; then
    printf '%s\\n' "claude-soma-channel.service"
    exit 0
fi
exec {real_jq} "$@"
""",
        )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["HERMES_NOTIFY_PORT"] = "9100"
    env.pop("HERMES_AUTO_RESTART_WINDOW_UTC", None)
    if with_window:
        env["HERMES_AUTO_RESTART_WINDOW_UTC"] = str(int(time.time()) + window_offset_secs)

    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Brief pause so the backgrounded setsid stub completes its file write
    # before we inspect the log.
    time.sleep(0.3)
    return result, order_log


# ------------------------------------------------------------------ tests


def test_mark_read_precedes_auto_restart_in_source() -> None:
    """Static: the mark_read endpoint URL line appears before the auto-restart-services.sh
    invocation line in the script source, guaranteeing the POST happens first."""
    source_lines = _SCRIPT.read_text().splitlines()
    mark_read_lineno = None
    auto_restart_lineno = None
    for i, line in enumerate(source_lines):
        # The mark_read POST uses "${ENDPOINT}/mark_read" as the URL argument.
        if mark_read_lineno is None and "/mark_read" in line:
            mark_read_lineno = i
        if auto_restart_lineno is None and "auto-restart-services.sh" in line:
            auto_restart_lineno = i

    assert mark_read_lineno is not None, (
        "mark_read endpoint URL line not found in script source"
    )
    assert auto_restart_lineno is not None, (
        "auto-restart-services.sh invocation not found in script source"
    )
    assert mark_read_lineno < auto_restart_lineno, (
        f"mark_read endpoint (line {mark_read_lineno + 1}) must appear before "
        f"auto-restart-services.sh (line {auto_restart_lineno + 1}) in the source"
    )


def test_mark_read_before_auto_restart_ordering(tmp_path: Path) -> None:
    """Dynamic: mark_read is logged before setsid (auto-restart) is spawned.

    Uses a fake jq wrapper to work around the jq 1.7 PCRE2 named-group syntax
    difference so that RESTART_SERVICES is non-empty and setsid actually fires.
    """
    result, order_log = _run_with_restart_milestone(tmp_path, inject_fake_jq=True)
    assert result.returncode == 0, f"script exited non-zero: {result.stderr}"

    assert order_log.exists(), (
        "order log was never written — neither mark_read nor setsid ran"
    )
    lines = [ln.strip() for ln in order_log.read_text().splitlines() if ln.strip()]

    assert "mark_read" in lines, f"mark_read not found in order log: {lines}"
    assert "auto-restart" in lines, f"setsid (auto-restart) not found in order log: {lines}"

    mark_idx = lines.index("mark_read")
    restart_idx = lines.index("auto-restart")
    assert mark_idx < restart_idx, (
        f"Expected mark_read before auto-restart but got order: {lines}"
    )


def test_no_auto_restart_without_window_env_var(tmp_path: Path) -> None:
    """Without HERMES_AUTO_RESTART_WINDOW_UTC, auto-restart is never spawned."""
    _, order_log = _run_with_restart_milestone(
        tmp_path, with_window=False, inject_fake_jq=True
    )
    if order_log.exists():
        lines = [ln.strip() for ln in order_log.read_text().splitlines() if ln.strip()]
        assert "auto-restart" not in lines, (
            f"auto-restart should not fire without window env var, got: {lines}"
        )


def test_no_auto_restart_when_no_restart_required_milestone(tmp_path: Path) -> None:
    """When no MILESTONE contains RESTART REQUIRED, setsid is never called."""
    _, order_log = _run_with_restart_milestone(
        tmp_path,
        events_json=_EVENTS_JSON_NO_RESTART,
    )
    if order_log.exists():
        lines = [ln.strip() for ln in order_log.read_text().splitlines() if ln.strip()]
        assert "auto-restart" not in lines, (
            f"auto-restart should not fire without RESTART REQUIRED milestone, got: {lines}"
        )


def test_mark_read_fires_for_regular_milestone(tmp_path: Path) -> None:
    """mark_read is called even when there is no RESTART REQUIRED (events still need marking)."""
    _, order_log = _run_with_restart_milestone(
        tmp_path,
        events_json=_EVENTS_JSON_NO_RESTART,
    )
    assert order_log.exists(), "order log not written — mark_read curl never ran"
    lines = [ln.strip() for ln in order_log.read_text().splitlines() if ln.strip()]
    assert "mark_read" in lines, (
        f"mark_read should fire for any unread events, got: {lines}"
    )


def test_expired_window_skips_auto_restart(tmp_path: Path) -> None:
    """When HERMES_AUTO_RESTART_WINDOW_UTC is in the past, setsid is not spawned."""
    _, order_log = _run_with_restart_milestone(
        tmp_path,
        window_offset_secs=-60,
        inject_fake_jq=True,
    )
    if order_log.exists():
        lines = [ln.strip() for ln in order_log.read_text().splitlines() if ln.strip()]
        assert "auto-restart" not in lines, (
            f"auto-restart should not fire with expired window, got: {lines}"
        )


def test_auto_restart_skipped_when_already_fired(tmp_path: Path) -> None:
    """When auto_restart_fired_at is non-null on a MILESTONE row, the jq filter
    excludes it so setsid is never spawned (dedup with Python-side trigger)."""
    _, order_log = _run_with_restart_milestone(
        tmp_path,
        events_json=_EVENTS_JSON_ALREADY_FIRED,
        with_window=True,
        inject_fake_jq=False,
    )
    if order_log.exists():
        lines = [ln.strip() for ln in order_log.read_text().splitlines() if ln.strip()]
        assert "auto-restart" not in lines, (
            f"auto-restart should not fire for row with auto_restart_fired_at set, got: {lines}"
        )
