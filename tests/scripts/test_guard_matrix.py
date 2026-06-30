"""Guard-matrix harness for scripts/remote-exec-guard.sh — drives the forced
command via SSH_ORIGINAL_COMMAND and asserts ACCEPT/DENY + side effects on a
private tmux socket. Covers the Phase-2 `send` verb (no sudo needed).

`tail-log` cases live alongside once Phase 2b lands.
"""
from __future__ import annotations

import base64
import os
import subprocess
import time

import pytest

GUARD = "/opt/claude-soma/scripts/remote-exec-guard.sh"
TMUX = "/usr/bin/tmux"
TN = "guardmatrixtest"  # NAME_RX-valid; isolated from real leads
SOCK = f"soma-lead-{TN}"
SESS = f"soma-proj-{TN}"

pytestmark = pytest.mark.skipif(
    not os.path.exists(GUARD) or not os.path.exists(TMUX),
    reason="guard or tmux not present",
)


def _run_guard(line: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SSH_ORIGINAL_COMMAND": line,
           "SSH_CONNECTION": "100.103.37.115 1 100.102.145.110 22"}
    return subprocess.run(["bash", GUARD], env=env, capture_output=True,
                          text=True, timeout=20)


def b64(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode()
    return base64.b64encode(s).decode("ascii")


@pytest.fixture()
def live_session(tmp_path):
    """A private tmux session running `cat > outfile`, so send-keys input that is
    submitted with Enter is captured line-by-line into the file."""
    out = tmp_path / "pane.out"
    subprocess.run([TMUX, "-L", SOCK, "kill-session", "-t", SESS],
                   capture_output=True)
    subprocess.run([TMUX, "-L", SOCK, "new-session", "-d", "-s", SESS,
                    f"cat > {out}"], check=True, capture_output=True)
    time.sleep(0.3)
    yield out
    subprocess.run([TMUX, "-L", SOCK, "kill-session", "-t", SESS],
                   capture_output=True)


# ---- send: ACCEPT + side effect -------------------------------------------

def test_send_delivers_message_and_enter(live_session):
    msg = "hello from A — verify delivery"
    cp = _run_guard(f"send {TN} {b64(msg)}")
    assert cp.returncode == 0, cp.stderr
    time.sleep(0.4)
    assert live_session.read_text().rstrip("\n") == msg  # MSG + Enter submitted


def test_send_leading_dash_message(live_session):
    msg = "--property=User=root attempt as TEXT"  # must land verbatim, not a flag
    cp = _run_guard(f"send {TN} {b64(msg)}")
    assert cp.returncode == 0, cp.stderr
    time.sleep(0.4)
    assert live_session.read_text().rstrip("\n") == msg


def test_send_b64_containing_execstartpre_accepts(live_session):
    # base64 payload may incidentally contain "ExecStartPre"; the escalation scan
    # must NOT scan the b64 token (regression for the :0:5 window fix).
    msg = "ExecStartPre is just normal words here"
    payload = b64(msg)
    assert "ExecStartPre" in msg
    cp = _run_guard(f"send {TN} {payload}")
    assert cp.returncode == 0, cp.stderr
    time.sleep(0.4)
    assert live_session.read_text().rstrip("\n") == msg


# ---- send: DENY -----------------------------------------------------------

@pytest.mark.parametrize("msg_bytes,why", [
    (b"line1\nline2", "newline (would submit early / inject 2nd prompt)"),
    (b"esc\x1bseq", "ESC control byte"),
    (b"ctrl\x03c", "ETX/^C control byte"),
    (b"nul\x00byte", "NUL byte"),
])
def test_send_denies_control_bytes(live_session, msg_bytes, why):
    cp = _run_guard(f"send {TN} {b64(msg_bytes)}")
    assert cp.returncode == 99, f"expected DENY for {why}: rc={cp.returncode}"
    assert "DENY" in cp.stderr


def test_send_denies_bad_name():
    cp = _run_guard(f"send Bad_Name {b64('hi')}")
    assert cp.returncode == 99 and "DENY" in cp.stderr


def test_send_denies_wrong_argcount():
    cp = _run_guard(f"send {TN}")  # missing b64
    assert cp.returncode == 99 and "DENY" in cp.stderr


def test_send_denies_non_base64():
    cp = _run_guard(f"send {TN} not*base64!")
    assert cp.returncode == 99 and "DENY" in cp.stderr


def test_send_denies_no_live_session():
    # No session created → guard's internal has-session check must DENY.
    subprocess.run([TMUX, "-L", SOCK, "kill-session", "-t", SESS], capture_output=True)
    cp = _run_guard(f"send {TN} {b64('hi')}")
    assert cp.returncode == 99 and "DENY" in cp.stderr
