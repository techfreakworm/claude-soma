"""Tests for scripts/engagement-hourly-drip.py and bash helper scripts."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DRIP_SCRIPT = SCRIPTS_DIR / "engagement-hourly-drip.py"
APPROVE_SCRIPT = SCRIPTS_DIR / "engagement-approve.sh"
POSTED_SCRIPT = SCRIPTS_DIR / "engagement-posted.sh"
DECLINE_SCRIPT = SCRIPTS_DIR / "engagement-decline.sh"
SERVICE_FILE = REPO_ROOT / "systemd" / "claude-soma-engagement-drip.service"
TIMER_FILE = REPO_ROOT / "systemd" / "claude-soma-engagement-drip.timer"

_spec = importlib.util.spec_from_file_location("engagement_drip", DRIP_SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


def _entry(
    entry_id: str,
    platform: str,
    status: str = "queued",
    queued_at: float | None = None,
    **kwargs,
) -> dict:
    base: dict = {
        "id": entry_id,
        "platform": platform,
        "source_permalink": f"https://example.com/{entry_id}",
        "source_author": f"author_{entry_id}",
        "source_excerpt": f"Excerpt for {entry_id}",
        "why_engage": "good opportunity",
        "draft_text": f"Draft reply for {entry_id}",
        "status": status,
        "queued_at": queued_at if queued_at is not None else time.time() - 3600,
        "released_at": None,
        "approved_at": None,
        "posted_at": None,
        "post_permalink": None,
        "post_error": None,
        "declined_at": None,
        "decline_reason": None,
    }
    base.update(kwargs)
    return base


def _cfg(tmp_path: Path, **overrides) -> dict:
    defaults = {
        "queue_path": str(tmp_path / "queue.jsonl"),
        "pause_path": str(tmp_path / "PAUSE"),
        "refill_flag": str(tmp_path / "REFILL_NEEDED"),
        "refill_threshold": 6,
        "review_page": str(tmp_path / "relay" / "engagement-review.md"),
        "log_path": str(tmp_path / "drip.log"),
        "tg_token": "",
        "tg_chat_id": "",
        "review_url": "https://files.mayankgupta.in/engagement-review.md",
    }
    defaults.update(overrides)
    return defaults


def _write_queue(path: Path | str, entries: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _read_queue(path: Path | str) -> list[dict]:
    return _mod.read_queue(str(path))


def _run_helper(script: Path, args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_QUEUE": str(tmp_path / "queue.jsonl"),
        "HERMES_ENGAGEMENT_REVIEW_PAGE": str(tmp_path / "relay" / "engagement-review.md"),
        "HERMES_ENGAGEMENT_LOG": str(tmp_path / "drip.log"),
        "HERMES_ENGAGEMENT_REVIEW_URL": "https://files.mayankgupta.in/engagement-review.md",
        "HERMES_ENGAGEMENT_REFILL_FLAG": str(tmp_path / "REFILL_NEEDED"),
        "HERMES_ENGAGEMENT_REFILL_THRESHOLD": "6",
        "TELEGRAM_BOT_TOKEN": "",
        "HERMES_NOTIFY_CHAT_ID": "",
    }
    return subprocess.run(
        ["bash", str(script)] + args,
        env=env,
        capture_output=True,
        text=True,
    )


# ────────────────────────────────────────────────────────────────────────────
# Drip logic tests
# ────────────────────────────────────────────────────────────────────────────


def test_pause_flag_no_op(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    pause = Path(cfg["pause_path"])
    pause.parent.mkdir(parents=True, exist_ok=True)
    pause.touch()

    entries = [_entry("id1", "x"), _entry("id2", "linkedin")]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.drip(cfg)

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    assert all(e["status"] == "queued" for e in after), "PAUSE must prevent any mutation"
    assert not Path(cfg["review_page"]).exists(), "review page must not be written"


def test_empty_queue_no_op(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    Path(cfg["queue_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["queue_path"]).write_text("")

    with patch("urllib.request.urlopen") as mock_open:
        result = _mod.drip(cfg)

    assert result == 0
    assert not mock_open.called, "Telegram DM must not fire on empty queue"


def test_drips_one_x_one_linkedin(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999")
    entries = [
        _entry("x-old", "x", queued_at=1000.0),
        _entry("x-new", "x", queued_at=2000.0),
        _entry("li-old", "linkedin", queued_at=1000.0),
        _entry("li-new", "linkedin", queued_at=2000.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip(cfg)

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    by_id = {e["id"]: e for e in after}

    assert by_id["x-old"]["status"] == "pending_review", "oldest X must be popped"
    assert by_id["x-new"]["status"] == "queued", "newer X must stay queued"
    assert by_id["li-old"]["status"] == "pending_review", "oldest LI must be popped"
    assert by_id["li-new"]["status"] == "queued", "newer LI must stay queued"

    assert mock_open.called, "Telegram urlopen must be called"
    req = mock_open.call_args[0][0]
    assert "api.telegram.org" in req.full_url
    assert "sendMessage" in req.full_url
    body = req.data.decode()
    assert "chat_id=999" in body or "chat_id" in body


def test_drips_only_one_platform_if_other_empty(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [
        _entry("x1", "x", queued_at=1000.0),
        _entry("x2", "x", queued_at=2000.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.drip(cfg)

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    by_id = {e["id"]: e for e in after}
    assert by_id["x1"]["status"] == "pending_review"
    assert by_id["x2"]["status"] == "queued"


def test_queue_low_writes_refill_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, refill_threshold=6)
    entries = [
        _entry(f"x{i}", "x", queued_at=float(i)) for i in range(3)
    ] + [
        _entry(f"li{i}", "linkedin", queued_at=float(i)) for i in range(2)
    ]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.drip(cfg)

    assert result == 0
    flag = Path(cfg["refill_flag"])
    assert flag.exists(), "REFILL_NEEDED must be written when count < threshold"
    flag_data = json.loads(flag.read_text())
    assert flag_data["remaining_queued"] < 6


def test_queue_full_removes_refill_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, refill_threshold=6)
    flag = Path(cfg["refill_flag"])
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text('{"ts":0,"remaining_queued":2,"breakdown":{"x":1,"linkedin":1}}\n')

    entries = [
        _entry(f"x{i}", "x", queued_at=float(i)) for i in range(10)
    ] + [
        _entry(f"li{i}", "linkedin", queued_at=float(i)) for i in range(10)
    ]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.drip(cfg)

    assert result == 0
    assert not flag.exists(), "REFILL_NEEDED must be removed when count >= threshold"


def test_review_page_rendered_with_pending_section(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [
        _entry("x-id", "x", queued_at=1000.0),
        _entry("li-id", "linkedin", queued_at=1000.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.drip(cfg)

    assert result == 0
    review = Path(cfg["review_page"])
    assert review.exists(), "review page must be created"
    content = review.read_text()
    assert "## Pending Review (2)" in content
    assert "### #x-id" in content
    assert "### #li-id" in content


def test_review_page_recently_posted_table(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [
        _entry(
            "post-id",
            "x",
            status="posted",
            posted_at=time.time() - 100,
            post_permalink="https://x.com/handle/status/999",
        ),
        _entry("q1", "x", queued_at=1000.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.drip(cfg)

    assert result == 0
    content = Path(cfg["review_page"]).read_text()
    assert "## Recently posted" in content
    assert "https://x.com/handle/status/999" in content
    assert "| post-id |" in content


def test_atomic_write_no_partial_on_crash(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    original_entries = [_entry("orig", "x")]
    _mod.write_queue_atomic(original_entries, queue)
    original_text = queue.read_text()

    with patch("os.replace", side_effect=OSError("simulated disk full")):
        with pytest.raises(OSError):
            _mod.write_queue_atomic([_entry("new", "linkedin")], queue)

    assert queue.read_text() == original_text, "original queue must be unchanged after failed atomic write"
    tmp_file = tmp_path / "queue.jsonl.tmp"
    assert tmp_file.exists(), ".tmp file should exist (orphaned)"


def test_regen_only_no_queue_mutation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [_entry("id1", "x", status="pending_review")]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.regen_only(cfg)

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    assert after[0]["status"] == "pending_review", "regen_only must not mutate queue"
    assert Path(cfg["review_page"]).exists()


# ────────────────────────────────────────────────────────────────────────────
# Approve / posted / decline function tests
# ────────────────────────────────────────────────────────────────────────────


def test_approve_entries_sets_approved_at(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [_entry("id1", "x", status="pending_review")]
    _write_queue(cfg["queue_path"], entries)

    before = time.time()
    result = _mod.approve_entries(cfg, ids=["id1"])
    after_t = time.time()

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    assert after[0]["status"] == "approved"
    assert after[0]["approved_at"] is not None
    assert before <= after[0]["approved_at"] <= after_t


def test_approve_all_entries(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [
        _entry("id1", "x", status="pending_review"),
        _entry("id2", "linkedin", status="pending_review"),
        _entry("id3", "x", status="queued"),
    ]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.approve_entries(cfg, all_pending=True)

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    by_id = {e["id"]: e for e in after}
    assert by_id["id1"]["status"] == "approved"
    assert by_id["id2"]["status"] == "approved"
    assert by_id["id3"]["status"] == "queued", "queued entries must not be touched by approve_all"


def test_posted_helper_records_permalink(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [_entry("id1", "x", status="approved")]
    _write_queue(cfg["queue_path"], entries)

    permalink = "https://x.com/user/status/12345"
    result = _mod.mark_posted(cfg, "id1", permalink)

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    assert after[0]["status"] == "posted"
    assert after[0]["post_permalink"] == permalink
    assert after[0]["posted_at"] is not None


def test_posted_error_sets_failed_status(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [_entry("id1", "x", status="approved")]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.mark_posted_error(cfg, "id1", "rate limited")

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    assert after[0]["status"] == "failed"
    assert after[0]["post_error"] == "rate limited"


def test_decline_entry_sets_declined(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    entries = [_entry("id1", "x", status="pending_review")]
    _write_queue(cfg["queue_path"], entries)

    result = _mod.decline_entry(cfg, "id1", reason="off-brand")

    assert result == 0
    after = _read_queue(cfg["queue_path"])
    assert after[0]["status"] == "declined"
    assert after[0]["decline_reason"] == "off-brand"
    assert after[0]["declined_at"] is not None


# ────────────────────────────────────────────────────────────────────────────
# Bash helper subprocess tests
# ────────────────────────────────────────────────────────────────────────────


def test_approve_helper_sets_approved_at(tmp_path: Path) -> None:
    entries = [_entry("h-id1", "x", status="pending_review")]
    _write_queue(tmp_path / "queue.jsonl", entries)

    result = _run_helper(APPROVE_SCRIPT, ["h-id1"], tmp_path)

    assert result.returncode == 0, result.stderr
    after = _read_queue(tmp_path / "queue.jsonl")
    assert after[0]["status"] == "approved"
    assert after[0]["approved_at"] is not None and after[0]["approved_at"] > 0


def test_approve_all_helper(tmp_path: Path) -> None:
    entries = [
        _entry("h1", "x", status="pending_review"),
        _entry("h2", "linkedin", status="pending_review"),
        _entry("h3", "x", status="pending_review"),
    ]
    _write_queue(tmp_path / "queue.jsonl", entries)

    result = _run_helper(APPROVE_SCRIPT, ["--all"], tmp_path)

    assert result.returncode == 0, result.stderr
    after = _read_queue(tmp_path / "queue.jsonl")
    assert all(e["status"] == "approved" for e in after)
    assert "Approved 3 entries" in result.stdout


def test_posted_helper_records_permalink_subprocess(tmp_path: Path) -> None:
    entries = [_entry("p-id1", "x", status="approved")]
    _write_queue(tmp_path / "queue.jsonl", entries)

    permalink = "https://x.com/testuser/status/9999"
    result = _run_helper(POSTED_SCRIPT, ["p-id1", permalink], tmp_path)

    assert result.returncode == 0, result.stderr
    after = _read_queue(tmp_path / "queue.jsonl")
    assert after[0]["status"] == "posted"
    assert after[0]["post_permalink"] == permalink


# ────────────────────────────────────────────────────────────────────────────
# Telegram mock test
# ────────────────────────────────────────────────────────────────────────────


def test_telegram_dm_invoked_with_correct_url(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, tg_token="bot-token-123", tg_chat_id="777888")
    entries = [
        _entry("x1", "x", queued_at=1000.0),
        _entry("l1", "linkedin", queued_at=1000.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        _mod.drip(cfg)

    assert mock_open.called
    req = mock_open.call_args[0][0]
    assert "https://api.telegram.org/botbot-token-123/sendMessage" == req.full_url
    body = urllib.parse.parse_qs(req.data.decode())
    assert body.get("chat_id") == ["777888"]
    assert "777888" in req.data.decode()


# ────────────────────────────────────────────────────────────────────────────
# systemd unit shape tests
# ────────────────────────────────────────────────────────────────────────────


def test_service_file_exec_path_legacy_skip() -> None:
    """Pre-FI-ENGAGEMENT-FRESH-DRIP this test pinned the direct drip ExecStart;
    the service now invokes the dispatcher instead. Covered by
    test_service_calls_dispatcher_not_drip_directly above."""
    pytest.skip("superseded by test_service_calls_dispatcher_not_drip_directly")


def _legacy_test_service_file_exec_path() -> None:
    content = SERVICE_FILE.read_text()
    assert "ExecStart=/opt/claude-soma/scripts/engagement-hourly-drip.py" in content
    assert "User=ubuntu" in content
    assert "Type=oneshot" in content


def test_timer_file_hourly() -> None:
    content = TIMER_FILE.read_text()
    assert "OnCalendar=hourly" in content
    assert "Persistent=true" in content
    assert "WantedBy=timers.target" in content


def test_drip_script_executable() -> None:
    assert os.access(str(DRIP_SCRIPT), os.X_OK), "drip script must have executable bit"


def test_approve_script_executable() -> None:
    assert os.access(str(APPROVE_SCRIPT), os.X_OK)


def test_posted_script_executable() -> None:
    assert os.access(str(POSTED_SCRIPT), os.X_OK)


def test_decline_script_executable() -> None:
    assert os.access(str(DECLINE_SCRIPT), os.X_OK)


def test_bash_syntax_approve() -> None:
    r = subprocess.run(["bash", "-n", str(APPROVE_SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_bash_syntax_posted() -> None:
    r = subprocess.run(["bash", "-n", str(POSTED_SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_bash_syntax_decline() -> None:
    r = subprocess.run(["bash", "-n", str(DECLINE_SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# need to import urllib.parse for the telegram test assertion
import urllib.parse  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# FI-ENGAGEMENT-FRESH-DRIP tests (--source=fresh / --fallback / dispatcher)
# ────────────────────────────────────────────────────────────────────────────


DISPATCH_SCRIPT = SCRIPTS_DIR / "engagement-hourly-dispatch.sh"
SUBAGENT_PROMPT = SCRIPTS_DIR / "engagement-browse-draft-subagent.txt"
SUBAGENT_MCP = REPO_ROOT / "config" / "claude" / "engagement-subagent-mcp.json"


def test_dispatch_script_executable_and_syntax() -> None:
    assert DISPATCH_SCRIPT.is_file(), "dispatcher script must exist"
    assert os.access(str(DISPATCH_SCRIPT), os.X_OK), "dispatcher must be +x"
    r = subprocess.run(
        ["bash", "-n", str(DISPATCH_SCRIPT)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def test_subagent_prompt_has_required_placeholders() -> None:
    body = SUBAGENT_PROMPT.read_text()
    for ph in ("__START_ISO__", "__START_TS__", "__QUEUE_PATH__"):
        assert ph in body, f"subagent prompt missing placeholder {ph}"


def test_subagent_mcp_config_is_minimal() -> None:
    cfg = json.loads(SUBAGENT_MCP.read_text())
    servers = set(cfg["mcpServers"].keys())
    # The whole point of the subagent is a tight tool surface — assert no
    # voice / project-orchestrator / general playwright leak into it.
    forbidden = {"voice-stt", "voice-tts", "project-orchestrator", "playwright"}
    leaks = servers & forbidden
    assert not leaks, f"unexpected MCP servers in subagent config: {leaks}"
    # After FI-ENGAGEMENT-FRESH-DRIP-AUTH the subagent does NOT use the
    # playwright MCP for browsing (the MCP-driven path opens a fresh
    # unauthenticated context in subagent/dispatched contexts and harvests
    # zero posts — same finding documented in engagement-post-x.js header).
    # The browse path goes through engagement-browse-x.js / -linkedin.js
    # driven via Bash, which load the same storageState files the post
    # scripts use. Only hermes-api remains here.
    assert "playwright-x" not in servers, (
        "playwright-x MCP must NOT be in the subagent config — see "
        "FI-ENGAGEMENT-FRESH-DRIP-AUTH 2026-06-05"
    )
    assert "playwright-linkedin" not in servers, (
        "playwright-linkedin MCP must NOT be in the subagent config — see "
        "FI-ENGAGEMENT-FRESH-DRIP-AUTH 2026-06-05"
    )
    assert "hermes-api" in servers, "hermes-api must remain for queue writes"


def test_subagent_prompt_routes_through_node_helpers() -> None:
    """Subagent prompt must instruct using the Node browse scripts and ban
    the playwright MCP path."""
    body = SUBAGENT_PROMPT.read_text()
    assert "engagement-browse-x.js" in body
    assert "engagement-browse-linkedin.js" in body
    # And must explicitly tell the subagent NOT to call playwright MCP tools.
    assert "DO NOT call any playwright MCP tools" in body or (
        "NEVER call playwright MCP tools" in body
    )


def test_browse_helpers_exist_and_executable() -> None:
    for name in ("engagement-browse-x.js", "engagement-browse-linkedin.js"):
        path = SCRIPTS_DIR / name
        assert path.is_file(), f"{name} must exist"
        assert os.access(str(path), os.X_OK), f"{name} must be +x"


def test_dispatcher_passes_bypass_permissions_to_subagent() -> None:
    """In `claude -p` headless mode the default permission gate makes every
    Bash call hang on approval, so node/openssl/printf are denied. The
    dispatcher must pass --permission-mode bypassPermissions; the
    orchestrator_gate.sh PreToolUse hook still enforces the denylist."""
    body = DISPATCH_SCRIPT.read_text()
    assert "--permission-mode bypassPermissions" in body, (
        "claude -p subagent must use --permission-mode bypassPermissions; "
        "otherwise every Bash tool call hangs on approval and the "
        "harvest+queue-append workflow can't run (live witness: "
        "2026-06-05T07:30Z run, subagent exited 0 with errors="
        "bash-permission-denied)"
    )


def test_browse_helpers_emit_source_permalink_field() -> None:
    """Browse helpers must emit the queue-compatible field name
    `source_permalink` (the post helpers look up the URL by THAT key).
    Live witness: 14:00 IST drafts had source_permalink=null and were
    un-postable until the field-name fix."""
    for name in ("engagement-browse-x.js", "engagement-browse-linkedin.js"):
        body = (SCRIPTS_DIR / name).read_text()
        assert "source_permalink" in body, (
            f"{name} must emit source_permalink — was source_post_url, "
            "and post helpers can't find the URL by the wrong key"
        )
        assert "source_post_url" not in body or (
            # ok if it only appears in a comment explicitly contrasting it
            "NOT `source_post_url`" in body or "not `source_post_url`" in body
        ), (
            f"{name} must NOT emit source_post_url (legacy field name); "
            "rename to source_permalink"
        )


def test_browse_helpers_emit_needs_reauth_signal() -> None:
    """Both browse helpers must emit a distinctive RESULT:NEEDS_REAUTH
    line when the storageState is rejected / login wall, NOT silently
    return RESULT:OK n=0 (the prior behavior masked an expired auth as a
    'feed is empty' state)."""
    for name in ("engagement-browse-x.js", "engagement-browse-linkedin.js"):
        body = (SCRIPTS_DIR / name).read_text()
        assert "RESULT:NEEDS_REAUTH" in body, (
            f"{name} must surface RESULT:NEEDS_REAUTH on login-wall detection"
        )


def test_li_browse_uses_new_mainFeed_selector() -> None:
    """The browse helper must target the new data-testid='mainFeed' wrapper
    (LinkedIn rotated CSS-modules class names so every legacy selector is
    dead). Root-cause documented 2026-06-05 from /var/log/claude-soma/li-diag/."""
    body = (SCRIPTS_DIR / "engagement-browse-linkedin.js").read_text()
    assert "data-testid=\"mainFeed\"" in body or 'data-testid="mainFeed"' in body, (
        "LinkedIn harvest must scope to [data-testid='mainFeed'] — legacy "
        "feed-shared-update / occludable-update class selectors are dead "
        "after LinkedIn's CSS-modules migration"
    )
    assert "Feed post" in body, (
        "LinkedIn cards are identified by innerText prefix 'Feed post ' "
        "(a11y contract); harvest must filter by that"
    )


def test_dispatcher_purges_null_permalink_drafts() -> None:
    """The dispatcher must purge any fresh draft with a null/empty
    source_permalink before counting + popping. Defense in depth on top of
    the subagent prompt — un-postable drafts must never reach pending_review."""
    body = DISPATCH_SCRIPT.read_text()
    assert "_purge_null_permalink_drafts" in body, (
        "dispatcher must define _purge_null_permalink_drafts"
    )
    assert "source_permalink" in body, (
        "dispatcher must reference source_permalink (the queue field name) "
        "in its purge + count logic"
    )


def test_dispatch_log_lines_are_newline_terminated(tmp_path: Path) -> None:
    """Each run must append a full line, not a fragment that concatenates
    with the previous run's JSON (live witness: pre-fix logs were one
    unbroken blob until 2026-06-05T07:48 fix)."""
    # Drive the fast-path skip branch three times and assert wc -l == 3.
    dlog = tmp_path / "dispatch.jsonl"
    qpath = tmp_path / "queue.jsonl"
    qpath.touch()
    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_FRESH_MODE": "off",  # fast-path → no subagent
        "HERMES_ENGAGEMENT_QUEUE": str(qpath),
        "HERMES_ENGAGEMENT_DISPATCH_LOG": str(dlog),
        "HERMES_ENGAGEMENT_LOG": str(tmp_path / "drip.log"),
        "HERMES_ENGAGEMENT_PYTHON": sys.executable,
        "TELEGRAM_BOT_TOKEN": "",
        "HERMES_NOTIFY_CHAT_ID": "",
    }
    for _ in range(3):
        subprocess.run(
            ["bash", str(DISPATCH_SCRIPT)],
            env=env,
            capture_output=True,
            check=False,
        )
    lines = dlog.read_text().splitlines()
    assert len(lines) == 3, (
        f"expected 3 separate JSON-per-line entries, got {len(lines)}; "
        f"newline-strip bug in _log_dispatch_line regressed"
    )
    for line in lines:
        json.loads(line)  # each line must parse as its own JSON object


def test_service_calls_dispatcher_not_drip_directly() -> None:
    content = SERVICE_FILE.read_text()
    assert "engagement-hourly-dispatch.sh" in content, (
        "the FI-ENGAGEMENT-FRESH-DRIP service must invoke the dispatcher"
    )
    # The dispatcher's job is to choose between drip --source=fresh and
    # drip --fallback; the service file shouldn't call the drip script
    # directly anymore.
    direct_calls = [
        line for line in content.splitlines()
        if line.startswith("ExecStart=") and "engagement-hourly-drip.py" in line
    ]
    assert not direct_calls, (
        "ExecStart should not call engagement-hourly-drip.py directly"
    )


# ── drip --source=fresh ────────────────────────────────────────────────────


def test_drip_source_fresh_pops_only_freshly_drafted(tmp_path: Path) -> None:
    """--source=fresh + --start-ts must filter to recently-drafted entries."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999")
    entries = [
        # Stale: queued long ago, no freshly_drafted_at
        _entry("x-stale", "x", queued_at=now - 7200),
        _entry("li-stale", "linkedin", queued_at=now - 7200),
        # Fresh: just drafted by the subagent
        _entry("x-fresh", "x", queued_at=now, freshly_drafted_at=now),
        _entry("li-fresh", "linkedin", queued_at=now, freshly_drafted_at=now),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip(cfg, source="fresh", start_ts=now - 60, banner="FRESH")

    assert result == 0
    after = {e["id"]: e for e in _read_queue(cfg["queue_path"])}
    assert after["x-fresh"]["status"] == "pending_review"
    assert after["li-fresh"]["status"] == "pending_review"
    assert after["x-stale"]["status"] == "queued", "stale must remain queued"
    assert after["li-stale"]["status"] == "queued"
    assert mock_open.called
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "FRESH" in body, "DM banner must say FRESH"


def test_drip_source_fresh_with_no_fresh_drafts_is_no_op(tmp_path: Path) -> None:
    """No freshly_drafted_at >= start_ts → nothing popped, no DM (by default)."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999")
    entries = [
        _entry("x-stale", "x", queued_at=now - 7200),
        _entry("li-stale", "linkedin", queued_at=now - 7200),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        result = _mod.drip(cfg, source="fresh", start_ts=now, banner="FRESH")

    assert result == 0
    assert not mock_open.called, "no DM when no fresh drafts (--source=fresh)"
    after = {e["id"]: e for e in _read_queue(cfg["queue_path"])}
    assert after["x-stale"]["status"] == "queued"


# ── drip --fallback ─────────────────────────────────────────────────────────


def test_drip_fallback_pops_pool_with_fallback_banner(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999")
    entries = [
        _entry("x1", "x", queued_at=time.time() - 3600),
        _entry("li1", "linkedin", queued_at=time.time() - 3600),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip(
            cfg,
            source="any",
            banner="POOLED FALLBACK",
            on_empty_emit_dm=True,
            fallback_reason="subagent_timeout",
        )

    assert result == 0
    raw = mock_open.call_args[0][0].data.decode()
    body = urllib.parse.unquote_plus(raw)
    assert "POOLED FALLBACK" in body
    assert "subagent_timeout" in body, "fallback_reason must surface in the DM"


def test_drip_fallback_empty_pool_still_sends_needs_intervention_dm(
    tmp_path: Path,
) -> None:
    """The silent-hour fix: even with zero drafts, fallback DMs the operator."""
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999")
    Path(cfg["queue_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["queue_path"]).write_text("")

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip(
            cfg,
            source="any",
            banner="POOLED FALLBACK",
            on_empty_emit_dm=True,
            fallback_reason="playwright_state_expired",
        )

    assert result == 0
    assert mock_open.called, (
        "empty-pool fallback MUST still send a DM — silent hour is a "
        "contract violation per FI-ENGAGEMENT-FRESH-DRIP sign-off"
    )
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "playwright_state_expired" in body
    assert "No engagement drafts this hour" in body


# ── main() flag parsing ────────────────────────────────────────────────────


def test_main_parses_source_fresh_and_start_ts(monkeypatch, tmp_path: Path) -> None:
    """CLI: --source=fresh --start-ts <epoch> threads through to drip()."""
    captured: dict = {}

    def fake_drip(cfg, **kwargs):
        captured["cfg"] = cfg
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(_mod, "drip", fake_drip)
    monkeypatch.setattr(sys, "argv", [
        "engagement-hourly-drip.py", "--source=fresh", "--start-ts", "1234567890",
    ])
    _mod._cfg = lambda: _cfg(tmp_path)  # type: ignore[attr-defined]
    rc = _mod.main()
    assert rc == 0
    assert captured["kwargs"]["source"] == "fresh"
    assert captured["kwargs"]["start_ts"] == 1234567890.0
    assert captured["kwargs"]["banner"] == "FRESH"


def test_main_parses_fallback_with_reason(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_drip(cfg, **kwargs):
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(_mod, "drip", fake_drip)
    monkeypatch.setattr(sys, "argv", [
        "engagement-hourly-drip.py",
        "--fallback", "--fallback-reason", "subagent_timeout",
    ])
    _mod._cfg = lambda: _cfg(tmp_path)  # type: ignore[attr-defined]
    rc = _mod.main()
    assert rc == 0
    assert captured["kwargs"]["source"] == "any"
    assert captured["kwargs"]["banner"] == "POOLED FALLBACK"
    assert captured["kwargs"]["on_empty_emit_dm"] is True
    assert captured["kwargs"]["fallback_reason"] == "subagent_timeout"


def test_main_no_flag_is_legacy_drip(monkeypatch, tmp_path: Path) -> None:
    """Back-compat: no flag = legacy mechanical drip, no fallback DM."""
    called_with: list = []

    def fake_drip(cfg, *args, **kwargs):
        called_with.append((args, kwargs))
        return 0

    monkeypatch.setattr(_mod, "drip", fake_drip)
    monkeypatch.setattr(sys, "argv", ["engagement-hourly-drip.py"])
    _mod._cfg = lambda: _cfg(tmp_path)  # type: ignore[attr-defined]
    rc = _mod.main()
    assert rc == 0
    assert called_with == [((), {})], (
        "legacy invocation must hit drip() with no kwargs"
    )
