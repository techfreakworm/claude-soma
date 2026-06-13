"""Tests for scripts/tg_reply_guard.py — Stop hook reply enforcement.

Runs the script as a subprocess with synthetic JSONL transcript fixtures.
No real network calls: curl is replaced by a stub on PATH that captures
invocations to a temp file.

Phase 0 test cases (plan Section 5):
  (a) channel turn with successful send -> allow (exit 0, no decision stdout)
  (b) channel turn, text only, MODE=block, not stop_hook_active -> stdout has
      {"decision":"block"}
  (c) same, MODE=enforce + stop_hook_active=true -> curl stub invoked with
      expected chat_id and body
  (d) notification-triggered turn (no <channel> string) -> allow
  (e) send tool_result is_error:true -> treated as no-send -> enforce fires
  (f) heard-pending flag present on enforce auto-relay -> body starts with Heard:
  (g) garbage jsonl lines -> fail-open (exit 0)
  (h) cwd != /opt/claude-soma -> allow immediately
  (i) MODE=log never blocks or sends, only writes telemetry

Each test class uses a unique session_id prefix to avoid cross-test contamination
via the /tmp dedup flag files.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "tg_reply_guard.py"


# ---------------------------------------------------------------------------
# Guard-flag cleanup fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_guard_flags():
    """Remove any leftover tg-guard and heard-pending flag files before and after each test.

    Prevents cross-test contamination: a test that triggers a successful-send write
    (which records the message_id in the guard flag) would silently skip enforcement
    checks in subsequent tests that reuse the same session_id + message_id pair.
    """
    _purge_guard_flags()
    yield
    _purge_guard_flags()


def _purge_guard_flags() -> None:
    for prefix in ("claude-soma-tg-guard-", "claude-soma-heard-pending-"):
        for p in Path("/tmp").glob(f"{prefix}*"):
            try:
                p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Transcript fixtures
# ---------------------------------------------------------------------------

CHANNEL_USER_ENTRY = {
    "type": "user",
    "message": {
        "role": "user",
        "content": (
            '<channel source="plugin:telegram:telegram" '
            'chat_id="12345" message_id="99">Hello bot</channel>'
        ),
    },
}

NOTIFICATION_USER_ENTRY = {
    "type": "user",
    "message": {
        "role": "user",
        "content": "Task f1-tracker COMPLETED. Result: analysis done.",
    },
}

ASSISTANT_TEXT_ENTRY = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "Here is my analysis for you."}],
    },
}

SEND_TOOL_USE_ENTRY = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "mcp__hermes_api__send_tg_reply",
                "input": {"chat_id": "12345", "text": "Done!"},
            }
        ],
    },
}

SEND_TOOL_RESULT_OK = {
    "type": "user",
    "message": {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_001",
                "is_error": False,
                "content": "Message sent",
            }
        ],
    },
}

SEND_TOOL_RESULT_ERROR = {
    "type": "user",
    "message": {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_001",
                "is_error": True,
                "content": "Tool denied by heard_gate",
            }
        ],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_transcript(tmp_path: Path, entries: list[dict]) -> Path:
    t = tmp_path / "transcript.jsonl"
    lines = [json.dumps(e) for e in entries]
    t.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return t


def _make_curl_stub(stub_dir: Path) -> tuple[Path, Path]:
    """Create a fake curl that appends the -d JSON payload to curl_capture.txt."""
    capture = stub_dir / "curl_capture.txt"
    stub = stub_dir / "curl"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            capture="{capture}"
            prev=""
            for arg in "$@"; do
                if [ "$prev" = "-d" ]; then
                    printf '%s\\n' "$arg" >> "$capture"
                fi
                prev="$arg"
            done
            printf '{{"ok":true}}'
            exit 0
            """
        )
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub, capture


def _make_secrets_env(secrets_dir: Path, token: str = "fake-token-test") -> Path:
    """Write a minimal secrets.env with a fake Telegram token."""
    s = secrets_dir / "secrets.env"
    s.write_text(f"TELEGRAM_BOT_TOKEN={token}\n", encoding="utf-8")
    return s


def _base_env(tmp_path: Path, mode: str, curl_stub_dir: Path | None = None) -> dict:
    env = os.environ.copy()
    env["SOMA_TG_REPLY_GUARD_MODE"] = mode
    env["HOME"] = str(tmp_path)
    if curl_stub_dir:
        env["PATH"] = f"{curl_stub_dir}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


def _run_guard(
    tmp_path: Path,
    transcript: Path | None,
    *,
    stop_hook_active: bool = False,
    mode: str = "log",
    cwd_override: str = "/opt/claude-soma",
    session_id: str,
    extra_env: dict | None = None,
    curl_stub_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run the script directly (no secrets.env patching — token absent = no curl)."""
    event = {
        "session_id": session_id,
        "transcript_path": str(transcript) if transcript else "",
        "cwd": cwd_override,
        "hook_event_name": "Stop",
        "stop_hook_active": stop_hook_active,
    }
    env = _base_env(tmp_path, mode, curl_stub_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _run_guard_with_secrets(
    tmp_path: Path,
    transcript: Path,
    *,
    secrets_env: Path,
    stop_hook_active: bool = False,
    session_id: str,
    curl_stub_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run via a wrapper that patches _read_secrets_var to use a test secrets file."""
    wrapper = tmp_path / f"wrap_{session_id.replace('-', '_').replace(' ', '_')}.py"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            import sys, importlib.util
            spec = importlib.util.spec_from_file_location("tg_reply_guard", r"{SCRIPT}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _orig = mod._read_secrets_var
            mod._read_secrets_var = lambda name, secrets=None: _orig(name, secrets=r"{secrets_env}")
            mod.main()
            """
        )
    )
    event = {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": "/opt/claude-soma",
        "hook_event_name": "Stop",
        "stop_hook_active": stop_hook_active,
    }
    env = _base_env(tmp_path, "enforce", curl_stub_dir)
    return subprocess.run(
        [sys.executable, str(wrapper)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# (a) Channel turn with successful send -> allow
# ---------------------------------------------------------------------------

class TestCaseA_SuccessfulSend:

    def test_exit_zero(self, tmp_path):
        t = _write_transcript(tmp_path, [
            CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY,
            SEND_TOOL_USE_ENTRY, SEND_TOOL_RESULT_OK,
        ])
        r = _run_guard(tmp_path, t, mode="enforce", session_id="a-exit-zero")
        assert r.returncode == 0

    def test_no_block_decision_on_stdout(self, tmp_path):
        t = _write_transcript(tmp_path, [
            CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY,
            SEND_TOOL_USE_ENTRY, SEND_TOOL_RESULT_OK,
        ])
        r = _run_guard(tmp_path, t, mode="enforce", session_id="a-no-block")
        stdout = r.stdout.strip()
        if stdout:
            assert json.loads(stdout).get("decision") != "block"

    def test_no_block_in_block_mode(self, tmp_path):
        t = _write_transcript(tmp_path, [
            CHANNEL_USER_ENTRY, SEND_TOOL_USE_ENTRY, SEND_TOOL_RESULT_OK,
        ])
        r = _run_guard(tmp_path, t, mode="block", session_id="a-block-mode")
        assert r.returncode == 0
        assert "block" not in r.stdout


# ---------------------------------------------------------------------------
# (b) Channel turn, text only, MODE=block, attempt 1 -> {"decision":"block"}
# ---------------------------------------------------------------------------

class TestCaseB_TextOnlyBlockMode:

    def test_block_decision_emitted(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="b-decision")
        assert r.returncode == 0
        assert r.stdout.strip(), "expected JSON on stdout"
        assert json.loads(r.stdout.strip()).get("decision") == "block"

    def test_block_reason_contains_chat_id(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="b-chatid")
        reason = json.loads(r.stdout.strip()).get("reason", "")
        assert "12345" in reason

    def test_block_reason_contains_message_id(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="b-msgid")
        reason = json.loads(r.stdout.strip()).get("reason", "")
        assert "99" in reason

    def test_block_reason_names_send_tool(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="b-toolname")
        reason = json.loads(r.stdout.strip()).get("reason", "")
        assert "send_tg_reply" in reason


# ---------------------------------------------------------------------------
# (c) MODE=enforce + stop_hook_active=true -> curl invoked with chat_id + body
# ---------------------------------------------------------------------------

class TestCaseC_EnforceAutoRelay:

    def test_curl_receives_correct_chat_id(self, tmp_path):
        stub_dir = tmp_path / "stubs_c1"
        stub_dir.mkdir()
        _stub, capture = _make_curl_stub(stub_dir)
        secrets_env = _make_secrets_env(tmp_path)
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])

        r = _run_guard_with_secrets(
            tmp_path, t,
            secrets_env=secrets_env,
            stop_hook_active=True,
            session_id="c-chatid",
            curl_stub_dir=stub_dir,
        )
        assert r.returncode == 0, f"stderr={r.stderr!r}"
        assert capture.exists(), "curl stub was not invoked"
        payload = json.loads(capture.read_text().strip())
        assert payload.get("chat_id") == "12345"

    def test_curl_body_contains_assistant_text(self, tmp_path):
        stub_dir = tmp_path / "stubs_c2"
        stub_dir.mkdir()
        _stub, capture = _make_curl_stub(stub_dir)
        secrets_env = _make_secrets_env(tmp_path)
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])

        r = _run_guard_with_secrets(
            tmp_path, t,
            secrets_env=secrets_env,
            stop_hook_active=True,
            session_id="c-body",
            curl_stub_dir=stub_dir,
        )
        assert r.returncode == 0
        assert capture.exists()
        payload = json.loads(capture.read_text().strip())
        body = payload.get("text", "")
        assert body.startswith("[auto-relay]")
        assert "Here is my analysis for you." in body

    def test_no_block_emitted_on_attempt2(self, tmp_path):
        stub_dir = tmp_path / "stubs_c3"
        stub_dir.mkdir()
        _make_curl_stub(stub_dir)
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="enforce", stop_hook_active=True,
                       curl_stub_dir=stub_dir, session_id="c-no-block")
        assert r.returncode == 0
        stdout = r.stdout.strip()
        if stdout:
            assert json.loads(stdout).get("decision") != "block"


# ---------------------------------------------------------------------------
# (d) Notification-triggered turn -> allow
# ---------------------------------------------------------------------------

class TestCaseD_NotificationTurn:

    def test_notification_turn_allowed(self, tmp_path):
        t = _write_transcript(tmp_path, [NOTIFICATION_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="enforce", session_id="d-notif")
        assert r.returncode == 0
        assert "block" not in r.stdout

    def test_local_tui_prompt_allowed(self, tmp_path):
        entry = {"type": "user", "message": {"role": "user", "content": "What is 2+2?"}}
        t = _write_transcript(tmp_path, [entry, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", session_id="d-tui")
        assert r.returncode == 0
        assert "block" not in r.stdout


# ---------------------------------------------------------------------------
# (e) send tool_result is_error:true -> treated as no-send -> gate fires
# ---------------------------------------------------------------------------

class TestCaseE_SendToolResultError:

    def test_error_result_treated_as_no_send(self, tmp_path):
        t = _write_transcript(tmp_path, [
            CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY,
            SEND_TOOL_USE_ENTRY, SEND_TOOL_RESULT_ERROR,
        ])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="e-error-result")
        assert r.returncode == 0
        assert r.stdout.strip(), "expected block decision when send has is_error:true"
        assert json.loads(r.stdout.strip()).get("decision") == "block"

    def test_plugin_telegram_reply_tool_counts_as_delivered(self, tmp_path):
        """mcp__plugin_telegram_telegram__reply (alternate tool name) also satisfies gate."""
        tool_use = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_002",
                 "name": "mcp__plugin_telegram_telegram__reply",
                 "input": {"chat_id": "12345", "text": "Hello"}},
            ]},
        }
        tool_result = {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_002",
                 "is_error": False, "content": "Sent"},
            ]},
        }
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, tool_use, tool_result])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="e-plugin-tool")
        assert r.returncode == 0
        assert "block" not in r.stdout

    def test_hermes_hyphen_spelling_counts_as_delivered(self, tmp_path):
        """mcp__hermes-api__send_tg_reply (hyphen, alternate spelling) satisfies gate."""
        tool_use = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_003",
                 "name": "mcp__hermes-api__send_tg_reply",
                 "input": {}},
            ]},
        }
        tool_result = {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_003",
                 "is_error": False, "content": "sent"},
            ]},
        }
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, tool_use, tool_result])
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="e-hyphen")
        assert r.returncode == 0
        assert "block" not in r.stdout


# ---------------------------------------------------------------------------
# (f) heard-pending flag present on enforce auto-relay -> body starts with Heard:
# ---------------------------------------------------------------------------

class TestCaseF_HeardPendingFlag:

    def test_heard_prefix_in_relay_body(self, tmp_path):
        stub_dir = tmp_path / "stubs_f"
        stub_dir.mkdir()
        _stub, capture = _make_curl_stub(stub_dir)
        secrets_env = _make_secrets_env(tmp_path)
        session_id = "f-heard-flag"

        heard_flag = Path(f"/tmp/claude-soma-heard-pending-{session_id}")
        heard_flag.write_text("This is what I heard", encoding="utf-8")

        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        try:
            r = _run_guard_with_secrets(
                tmp_path, t,
                secrets_env=secrets_env,
                stop_hook_active=True,
                session_id=session_id,
                curl_stub_dir=stub_dir,
            )
            assert r.returncode == 0, f"stderr={r.stderr!r}"
            assert capture.exists(), "curl stub was not invoked"
            payload = json.loads(capture.read_text().strip())
            body = payload.get("text", "")
            assert body.startswith("[auto-relay]"), f"body={body!r}"
            assert 'Heard:' in body
            assert "This is what I heard" in body
        finally:
            try:
                heard_flag.unlink()
            except OSError:
                pass

    def test_heard_flag_deleted_after_relay(self, tmp_path):
        """The heard-pending flag must be consumed (deleted) during auto-relay."""
        stub_dir = tmp_path / "stubs_f2"
        stub_dir.mkdir()
        _make_curl_stub(stub_dir)
        secrets_env = _make_secrets_env(tmp_path)
        session_id = "f-flag-delete"

        heard_flag = Path(f"/tmp/claude-soma-heard-pending-{session_id}")
        heard_flag.write_text("voice transcript here", encoding="utf-8")

        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        try:
            _run_guard_with_secrets(
                tmp_path, t,
                secrets_env=secrets_env,
                stop_hook_active=True,
                session_id=session_id,
                curl_stub_dir=stub_dir,
            )
            assert not heard_flag.exists(), "heard-pending flag should be deleted after relay"
        finally:
            try:
                heard_flag.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# (g) garbage jsonl lines -> fail-open (exit 0)
# ---------------------------------------------------------------------------

class TestCaseG_GarbageJsonl:

    def test_all_garbage_lines(self, tmp_path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("not json\n{broken\n!!@#$\n", encoding="utf-8")
        r = _run_guard(tmp_path, bad, mode="enforce", session_id="g-all-garbage")
        assert r.returncode == 0

    def test_mixed_good_and_garbage(self, tmp_path):
        t = tmp_path / "mixed.jsonl"
        t.write_text(
            json.dumps(CHANNEL_USER_ENTRY) + "\n"
            + "this is not json\n"
            + json.dumps(ASSISTANT_TEXT_ENTRY) + "\n"
            + "{{{bad\n",
            encoding="utf-8",
        )
        r = _run_guard(tmp_path, t, mode="block", stop_hook_active=False,
                       session_id="g-mixed")
        assert r.returncode == 0
        if r.stdout.strip():
            assert json.loads(r.stdout.strip()).get("decision") == "block"

    def test_nonexistent_transcript_allows(self, tmp_path):
        r = _run_guard(tmp_path, Path("/nonexistent/no.jsonl"), mode="enforce",
                       session_id="g-nonexistent")
        assert r.returncode == 0
        assert "block" not in r.stdout

    def test_empty_transcript_allows(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        r = _run_guard(tmp_path, empty, mode="enforce", session_id="g-empty")
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# (h) cwd != /opt/claude-soma -> allow immediately
# ---------------------------------------------------------------------------

class TestCaseH_NonBotCwd:

    def test_home_ubuntu_cwd_allows(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="enforce", cwd_override="/home/ubuntu",
                       session_id="h-home")
        assert r.returncode == 0
        assert "block" not in r.stdout

    def test_tmp_cwd_allows(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", cwd_override="/tmp",
                       session_id="h-tmp")
        assert r.returncode == 0
        assert "block" not in r.stdout

    def test_opt_other_cwd_allows(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="block", cwd_override="/opt/other",
                       session_id="h-opt-other")
        assert r.returncode == 0
        assert "block" not in r.stdout


# ---------------------------------------------------------------------------
# (i) MODE=log never blocks or sends — only telemetry
# ---------------------------------------------------------------------------

class TestCaseI_LogModeNeverBlocksOrSends:

    def test_no_block_on_text_only_turn(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="log", stop_hook_active=False,
                       session_id="i-no-block")
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_no_relay_on_stop_hook_active(self, tmp_path):
        stub_dir = tmp_path / "stubs_i"
        stub_dir.mkdir()
        _stub, capture = _make_curl_stub(stub_dir)
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="log", stop_hook_active=True,
                       curl_stub_dir=stub_dir, session_id="i-no-relay")
        assert r.returncode == 0
        assert not capture.exists(), "curl must NOT be invoked in log mode"
        assert r.stdout.strip() == ""

    def test_no_block_on_delivered_turn(self, tmp_path):
        t = _write_transcript(tmp_path, [
            CHANNEL_USER_ENTRY, SEND_TOOL_USE_ENTRY, SEND_TOOL_RESULT_OK,
        ])
        r = _run_guard(tmp_path, t, mode="log", session_id="i-delivered")
        assert r.returncode == 0
        assert "block" not in r.stdout


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

class TestKillSwitch:

    def test_disabled_env_bypasses_all(self, tmp_path):
        t = _write_transcript(tmp_path, [CHANNEL_USER_ENTRY, ASSISTANT_TEXT_ENTRY])
        r = _run_guard(tmp_path, t, mode="enforce", stop_hook_active=False,
                       session_id="ks-disabled",
                       extra_env={"SOMA_TG_REPLY_GUARD_DISABLED": "1"})
        assert r.returncode == 0
        assert "block" not in r.stdout


# ---------------------------------------------------------------------------
# Fail-open: garbage stdin
# ---------------------------------------------------------------------------

class TestGarbageStdin:

    def test_empty_stdin(self, tmp_path):
        r = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="",
            capture_output=True,
            text=True,
            env={**os.environ, "SOMA_TG_REPLY_GUARD_MODE": "enforce"},
            timeout=5,
        )
        assert r.returncode == 0

    def test_non_json_stdin(self, tmp_path):
        r = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="not json at all",
            capture_output=True,
            text=True,
            env={**os.environ, "SOMA_TG_REPLY_GUARD_MODE": "enforce"},
            timeout=5,
        )
        assert r.returncode == 0

    def test_partial_json_stdin(self, tmp_path):
        r = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input='{"session_id": "x"',  # truncated JSON
            capture_output=True,
            text=True,
            env={**os.environ, "SOMA_TG_REPLY_GUARD_MODE": "enforce"},
            timeout=5,
        )
        assert r.returncode == 0
