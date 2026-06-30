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


# ---- tail-log (fixed NAME-derived target; no caller path) ------------------

LOG_DIR = "/var/log/claude-soma"
_LOGW = os.access(LOG_DIR, os.W_OK) if os.path.isdir(LOG_DIR) else False


@pytest.fixture()
def transcript_log():
    path = f"{LOG_DIR}/{TN}.log"
    content = ("x" * 50 + "\nTAIL-LINE-1\nTAIL-LINE-2\n").encode()
    with open(path, "wb") as fh:
        fh.write(content)
    yield path, content
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.mark.skipif(not _LOGW, reason="/var/log/claude-soma not writable")
def test_tail_log_accepts_and_decodes(transcript_log):
    path, content = transcript_log
    cp = _run_guard(f"tail-log {TN}")
    assert cp.returncode == 0, cp.stderr
    got = base64.b64decode(cp.stdout.strip())
    assert got == content  # whole file fits under the cap


@pytest.mark.skipif(not _LOGW, reason="/var/log/claude-soma not writable")
def test_tail_log_denies_missing_transcript():
    try:
        os.unlink(f"{LOG_DIR}/{TN}.log")
    except OSError:
        pass
    cp = _run_guard(f"tail-log {TN}")
    assert cp.returncode == 99 and "DENY" in cp.stderr


def test_tail_log_denies_bad_name():
    cp = _run_guard("tail-log ../etc")
    assert cp.returncode == 99 and "DENY" in cp.stderr


def test_tail_log_denies_wrong_argcount():
    cp = _run_guard("tail-log")
    assert cp.returncode == 99 and "DENY" in cp.stderr


@pytest.mark.skipif(not _LOGW, reason="/var/log/claude-soma not writable")
def test_tail_log_denies_symlink_leaf(tmp_path):
    # Lead plants a symlink <name>.log -> a "secret"; O_NOFOLLOW must refuse it.
    secret = tmp_path / "secret"
    secret.write_text("TOP-SECRET-EXFIL-CANARY")
    log = f"{LOG_DIR}/{TN}.log"
    try:
        os.unlink(log)
    except OSError:
        pass
    os.symlink(str(secret), log)
    try:
        cp = _run_guard(f"tail-log {TN}")
        assert cp.returncode == 99 and "DENY" in cp.stderr
        assert "TOP-SECRET-EXFIL-CANARY" not in cp.stdout  # nothing leaked
    finally:
        os.unlink(log)


@pytest.mark.skipif(not _LOGW, reason="/var/log/claude-soma not writable")
def test_tail_log_denies_hardlink_leaf(tmp_path):
    # Hardlink keeps realpath in-dir (a naive symlink-only check misses it);
    # st_nlink>1 must refuse it.
    secret = tmp_path / "secret2"
    secret.write_text("HARDLINK-SECRET-CANARY")
    log = f"{LOG_DIR}/{TN}.log"
    try:
        os.unlink(log)
    except OSError:
        pass
    try:
        os.link(str(secret), log)  # hardlink (same fs); may fail cross-device
    except OSError:
        pytest.skip("cannot hardlink across filesystems in this env")
    try:
        cp = _run_guard(f"tail-log {TN}")
        assert cp.returncode == 99 and "DENY" in cp.stderr
        assert "HARDLINK-SECRET-CANARY" not in cp.stdout
    finally:
        os.unlink(log)


@pytest.mark.skipif(not _LOGW, reason="/var/log/claude-soma not writable")
def test_tail_log_denies_fifo_leaf_without_hanging():
    # A lead-planted FIFO would block a blocking open() forever (DoS). O_NONBLOCK
    # + S_ISREG must reject it fast.
    log = f"{LOG_DIR}/{TN}.log"
    try:
        os.unlink(log)
    except OSError:
        pass
    os.mkfifo(log)
    try:
        t0 = time.monotonic()
        cp = _run_guard(f"tail-log {TN}")          # _run_guard timeout=20 → a hang would fail
        assert time.monotonic() - t0 < 8, "tail-log hung on a FIFO leaf"
        assert cp.returncode == 99 and "DENY" in cp.stderr
    finally:
        os.unlink(log)
