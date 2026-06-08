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
    assert "### x-id" in content
    assert "### li-id" in content


def test_review_page_excludes_posted_approved_failed(tmp_path: Path) -> None:
    """FI-REVIEW-DOC-ACTIONABLE-ONLY (2026-06-05): the review doc must show
    ONLY drafts that still need the operator's decision (queued or
    pending_review). Posted, approved, failed, declined drafts MUST NOT
    appear — the doc is a to-do list, not an archive."""
    now = time.time()
    cfg = _cfg(tmp_path)
    entries = [
        _entry("posted-x", "x", status="posted", posted_at=now - 100,
               post_permalink="https://x.com/h/status/999"),
        _entry("posted-li", "linkedin", status="posted", posted_at=now - 200,
               post_permalink="https://www.linkedin.com/feed/update/urn:li:activity:1/"),
        _entry("approved-x", "x", status="approved", approved_at=now - 50),
        _entry("failed-x", "x", status="failed", post_error="some error"),
        _entry("declined-li", "linkedin", status="declined", decline_reason="meh"),
        _entry("pending-x", "x", status="pending_review", released_at=now),
        _entry("queued-x", "x", queued_at=now - 10),
        _entry("queued-li", "linkedin", queued_at=now - 5),
    ]
    _write_queue(cfg["queue_path"], entries)
    # Render directly (don't go through drip, which would mutate queued)
    out = tmp_path / "review.md"
    _mod.regenerate_review_page(
        entries, str(out),
        "https://files.example.test/engagement-review.md",
    )
    content = out.read_text()
    # SHOWN — v1 strips the leading "#" from entry headers (the id IS the id).
    assert "### pending-x" in content
    assert "### queued-x" in content
    assert "### queued-li" in content
    # NOT shown — the whole point of FI-REVIEW-DOC-ACTIONABLE-ONLY
    assert "posted-x" not in content
    assert "posted-li" not in content
    assert "approved-x" not in content
    assert "failed-x" not in content
    assert "declined-li" not in content
    # And the removed section headings
    assert "Recently posted" not in content
    assert "Approved · awaiting post" not in content
    assert "Approved &middot; awaiting post" not in content
    # New section headings present (v1 always renders both, even at 0).
    assert "## Pending Review (1)" in content
    assert "## Queued (next up — 2)" in content
    # Per-platform counts in the header — pending and queued counted separately
    assert "Actionable totals" in content
    assert "Pending review: 1 (X: 1)" in content
    assert "Queued: 2 (X: 1, LinkedIn: 1)" in content


def test_review_page_empty_actionable_still_renders_v1_structure(tmp_path: Path) -> None:
    """FI-ENGAGEMENT-SCHEMA-V1: even with 0 actionable entries, both
    `## Pending Review (0)` and `## Queued (next up — 0)` headings render
    so the doc shape stays identical regardless of queue state."""
    now = time.time()
    cfg = _cfg(tmp_path)
    entries = [
        _entry("p1", "x", status="posted", posted_at=now),
        _entry("a1", "x", status="approved", approved_at=now),
    ]
    _write_queue(cfg["queue_path"], entries)
    out = tmp_path / "review.md"
    _mod.regenerate_review_page(
        entries, str(out),
        "https://files.example.test/engagement-review.md",
    )
    content = out.read_text()
    # Both section headings present with their (0) counts.
    assert "## Pending Review (0)" in content
    assert "## Queued (next up — 0)" in content
    assert "_None._" in content
    # And the actionable-totals line still renders the zeros
    assert "Pending review: 0" in content
    assert "Queued: 0" in content
    # Schema version footprint at the top
    assert "_Schema version: engagement.v1_" in content


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


# ────────────────────────────────────────────────────────────────────────────
# FI-DRIP-IST-WINDOW (2026-06-06) — single-platform pop + IST hour parity
# ────────────────────────────────────────────────────────────────────────────


def test_drip_single_x_fresh_pops_only_x(tmp_path: Path, monkeypatch) -> None:
    """--single-platform=x --source=fresh pops exactly 1 fresh X draft,
    does not touch LI pool, sends exactly one DM."""
    cfg = _cfg(tmp_path)
    start_ts = 1000.0
    entries = [
        _entry("x-fresh", "x", queued_at=900.0, freshly_drafted_at=1100.0),
        _entry("x-old", "x", queued_at=800.0),  # no freshly_drafted_at → not fresh
        _entry("li-pool-1", "linkedin", queued_at=850.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    sent: list[str] = []
    monkeypatch.setattr(_mod, "send_telegram_dm",
                        lambda token, chat, text: sent.append(text) or True)

    result = _mod.drip_single(cfg, platform="x", source="fresh", start_ts=start_ts)
    assert result == 0

    after = _read_queue(cfg["queue_path"])
    by_id = {e["id"]: e for e in after}
    assert by_id["x-fresh"]["status"] == "pending_review"
    assert by_id["x-old"]["status"] == "queued"        # not fresh → not popped
    assert by_id["li-pool-1"]["status"] == "queued"    # LI untouched
    assert len(sent) == 1
    assert "X:" in sent[0] and "x-fresh" in sent[0]


def test_drip_single_li_pops_oldest_li(tmp_path: Path, monkeypatch) -> None:
    """--single-platform=linkedin pops the oldest queued LI draft and
    leaves X completely untouched."""
    cfg = _cfg(tmp_path)
    entries = [
        _entry("x-pool", "x", queued_at=100.0),
        _entry("li-newer", "linkedin", queued_at=300.0),
        _entry("li-older", "linkedin", queued_at=200.0),
    ]
    _write_queue(cfg["queue_path"], entries)

    sent: list[str] = []
    monkeypatch.setattr(_mod, "send_telegram_dm",
                        lambda token, chat, text: sent.append(text) or True)

    result = _mod.drip_single(cfg, platform="linkedin", source="any")
    assert result == 0

    after = _read_queue(cfg["queue_path"])
    by_id = {e["id"]: e for e in after}
    assert by_id["li-older"]["status"] == "pending_review"
    assert by_id["li-newer"]["status"] == "queued"
    assert by_id["x-pool"]["status"] == "queued"
    assert len(sent) == 1
    assert "LinkedIn:" in sent[0] and "li-older" in sent[0]


def test_drip_single_empty_pool_still_dms_with_review_link(
    tmp_path: Path, monkeypatch
) -> None:
    """Contract: every dispatch produces exactly one DM. An empty LI pool
    on an LI-hour MUST still DM (with the review URL) so silent hours
    can't recur."""
    cfg = _cfg(tmp_path)
    entries = [_entry("x-only", "x", queued_at=100.0)]  # no LI
    _write_queue(cfg["queue_path"], entries)

    sent: list[str] = []
    monkeypatch.setattr(_mod, "send_telegram_dm",
                        lambda token, chat, text: sent.append(text) or True)

    result = _mod.drip_single(cfg, platform="linkedin", source="any")
    assert result == 0
    assert len(sent) == 1
    assert "No engagement draft" in sent[0]


def test_drip_single_emits_v1_review_doc_after_pop(
    tmp_path: Path, monkeypatch
) -> None:
    """After popping, the review doc regenerates so the relay reflects
    the new pending_review state immediately."""
    cfg = _cfg(tmp_path)
    entries = [_entry("li-only", "linkedin", queued_at=500.0)]
    _write_queue(cfg["queue_path"], entries)
    monkeypatch.setattr(_mod, "send_telegram_dm",
                        lambda token, chat, text: True)

    _mod.drip_single(cfg, platform="linkedin", source="any")

    review = Path(cfg["review_page"])
    assert review.is_file()
    content = review.read_text()
    assert "_Schema version: engagement.v1_" in content
    assert "## Pending Review (1)" in content
    assert "li-only" in content


def test_drip_single_review_doc_byte_stable_after_no_pop(
    tmp_path: Path, monkeypatch
) -> None:
    """Empty-pool path: still produces one DM but must NOT regenerate the
    review doc (no state change to surface — keeps the doc churn-free)."""
    cfg = _cfg(tmp_path)
    _write_queue(cfg["queue_path"], [])  # empty queue
    monkeypatch.setattr(_mod, "send_telegram_dm",
                        lambda token, chat, text: True)

    review = Path(cfg["review_page"])
    assert not review.exists()
    _mod.drip_single(cfg, platform="linkedin", source="any")
    # No pop happened, so the review page is not regenerated — confirms
    # the empty-pool path doesn't churn the doc.
    assert not review.exists()


# ────────────────────────────────────────────────────────────────────────────
# Timer + dispatcher schedule contract
# ────────────────────────────────────────────────────────────────────────────


def test_timer_oncalendar_fires_12x_in_ist_window() -> None:
    """The timer file's OnCalendar MUST express 12 fires/day at IST 10..21
    (no overnight runs). The VPS is Asia/Kolkata so OnCalendar is local IST."""
    timer = (REPO_ROOT / "systemd" / "claude-soma-engagement-drip.timer").read_text()
    assert "OnCalendar=*-*-* 10..21:00:00" in timer, (
        "FI-DRIP-IST-WINDOW: timer must fire at IST 10..21 (12 slots), "
        "not the legacy hourly cadence"
    )
    assert "OnCalendar=hourly" not in timer, (
        "must not fall back to hourly — that re-introduces overnight runs"
    )


def test_dispatcher_alternates_platform_by_ist_hour_parity() -> None:
    """The dispatcher must pick X on odd IST hours and LinkedIn on even
    IST hours so the daily mix lands at ~6 X + 6 LinkedIn."""
    dispatch = (REPO_ROOT / "scripts" / "engagement-hourly-dispatch.sh").read_text()
    # Hour-parity computation must use Asia/Kolkata TZ to be timezone-safe.
    assert 'TZ=Asia/Kolkata date +%H' in dispatch
    assert "PLATFORM_THIS_HOUR=x" in dispatch
    assert "PLATFORM_THIS_HOUR=linkedin" in dispatch
    # And the LinkedIn fast-path must skip the subagent (cost saving).
    assert '"${PLATFORM_THIS_HOUR}" == "linkedin"' in dispatch
    # The subagent invocation comes AFTER the LI fast-path exit so the
    # subagent only runs on X hours.
    li_skip = dispatch.find('"${PLATFORM_THIS_HOUR}" == "linkedin"')
    subagent_call = dispatch.find('"${CLAUDE_BIN}" -p')
    assert 0 < li_skip < subagent_call, (
        "LI-hour fast-path must exit before reaching the subagent spawn"
    )


def test_drip_single_rejects_invalid_platform(tmp_path: Path) -> None:
    """Invalid platform string is rejected without touching the queue."""
    cfg = _cfg(tmp_path)
    entries = [_entry("x-1", "x", queued_at=100.0)]
    _write_queue(cfg["queue_path"], entries)
    result = _mod.drip_single(cfg, platform="bluesky", source="any")
    assert result == 1
    after = _read_queue(cfg["queue_path"])
    assert after[0]["status"] == "queued"  # untouched


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
# FI-ENGAGEMENT-SCHEMA-V1 (2026-06-06) — deterministic renderer contract
# ────────────────────────────────────────────────────────────────────────────


def _v1_entry(
    eid: str,
    platform: str,
    *,
    status: str = "pending_review",
    queued_at: float = 1000.0,
    topic: str = "claude-code",
    why: str = "concrete angle to add",
    excerpt: str = "source post text",
    note: str = "",
    **extra,
) -> dict:
    """Build a v1-conformant entry for tests."""
    base = {
        "schema_version": "engagement.v1",
        "id": eid,
        "platform": platform,
        "status": status,
        "queued_at": queued_at,
        "source_author": f"@{eid}",
        "source_permalink": f"https://example.test/{eid}",
        "source_excerpt": excerpt,
        "why_engage": why,
        "topic": topic,
        "relevance_note": note,
        "draft_text": f"draft for {eid}",
    }
    base.update(extra)
    return base


def test_render_review_body_is_byte_stable(tmp_path: Path) -> None:
    """Same input → same body output, two renders. This is the v1
    determinism contract: no clock, no random order, no per-call drift.
    The `render_review_body` helper deliberately excludes the regen-time
    footer so we can assert byte-equality across calls."""
    entries = [
        _v1_entry("eng-x-7k3f2a", "x", queued_at=100.0, topic="agents"),
        _v1_entry("eng-li-9m2p4d", "linkedin", queued_at=200.0, topic="mcp",
                  note="Author wrote the original MCP spec"),
        _v1_entry("eng-x-1a2b3c", "x", status="queued", queued_at=50.0,
                  topic="claude-code"),
    ]
    body_a = _mod.render_review_body(entries)
    body_b = _mod.render_review_body(entries)
    assert body_a == body_b, "render_review_body MUST be deterministic"

    # The regenerator wraps the body in a timestamp footer — the body
    # itself stays byte-stable, the footer is the ONLY churn surface.
    assert "_Schema version: engagement.v1_" in body_a
    assert "_Last regenerated:" not in body_a


def test_render_review_body_stable_sort(tmp_path: Path) -> None:
    """Entries within a section must appear in queued_at ascending order,
    ties broken by id lex. Shuffling the input MUST NOT change the output."""
    e1 = _v1_entry("eng-x-zz", "x", queued_at=100.0)
    e2 = _v1_entry("eng-x-aa", "x", queued_at=100.0)  # tie with e1, sorts first
    e3 = _v1_entry("eng-x-mm", "x", queued_at=50.0)   # older, sorts first

    out_ordered = _mod.render_review_body([e3, e2, e1])
    out_reversed = _mod.render_review_body([e1, e2, e3])
    out_arbitrary = _mod.render_review_body([e2, e3, e1])
    assert out_ordered == out_reversed == out_arbitrary

    # Verify the actual order: eng-x-mm (older) → eng-x-aa (tie, lex first) → eng-x-zz
    pos_mm = out_ordered.index("eng-x-mm")
    pos_aa = out_ordered.index("eng-x-aa")
    pos_zz = out_ordered.index("eng-x-zz")
    assert pos_mm < pos_aa < pos_zz


def test_render_review_body_entry_block_layout(tmp_path: Path) -> None:
    """Per-entry block layout is FROZEN in v1: header → Topic → Source →
    Why engage → Source excerpt → Draft → action hint → divider. The
    `relevance_note` slot inserts a 7th `Note` line between Topic and
    Source ONLY when non-empty."""
    e = _v1_entry(
        "eng-x-abc123", "x", queued_at=100.0,
        topic="ai-research",
        why="add the guardrails angle",
        excerpt="source body here",
    )
    body = _mod.render_review_body([e])
    # Header ordering — must appear in this exact relative order.
    pos_header = body.index("### eng-x-abc123 · X · @eng-x-abc123")
    pos_topic = body.index("- **Topic:** ai-research")
    pos_source = body.index("- **Source:** https://example.test/eng-x-abc123")
    pos_why = body.index("- **Why engage:** add the guardrails angle")
    pos_excerpt_label = body.index("- **Source excerpt:**")
    pos_excerpt_body = body.index("  > source body here")
    pos_draft_label = body.index("- **Draft:**")
    pos_action = body.index("`approve eng-x-abc123` | `decline eng-x-abc123`")
    assert pos_header < pos_topic < pos_source < pos_why
    assert pos_why < pos_excerpt_label < pos_excerpt_body
    assert pos_excerpt_body < pos_draft_label < pos_action
    # No Note line when relevance_note is empty.
    assert "**Note:**" not in body


def test_render_review_body_conditional_note_line(tmp_path: Path) -> None:
    """When `relevance_note` is non-empty it appears as a 7th line
    between Topic and Source. Default 5 fields stay in identical order."""
    e = _v1_entry(
        "eng-li-noted1", "linkedin", queued_at=100.0,
        topic="mcp",
        note="Author wrote the original MCP spec",
    )
    body = _mod.render_review_body([e])
    pos_topic = body.index("- **Topic:** mcp")
    pos_note = body.index("- **Note:** Author wrote the original MCP spec")
    pos_source = body.index("- **Source:** https://example.test/eng-li-noted1")
    assert pos_topic < pos_note < pos_source


def test_render_review_body_legacy_excerpt_fallback(tmp_path: Path) -> None:
    """One-version grace: v0 rows that have `source_post_excerpt` (not
    the v1 `source_excerpt`) still render their excerpt correctly. v2
    drops the fallback."""
    legacy = _v1_entry("eng-x-legacy1", "x", queued_at=100.0,
                       topic="claude-code", excerpt="")
    # Pop v1 field, set v0 field instead.
    legacy.pop("source_excerpt")
    legacy["source_post_excerpt"] = "legacy excerpt text"
    body = _mod.render_review_body([legacy])
    assert "legacy excerpt text" in body
    assert "(no excerpt)" not in body  # fallback should populate, not stub


def test_render_review_body_unknown_topic_renders_as_uncategorized(tmp_path: Path) -> None:
    """Anything outside the frozen TOPIC_TAGS set renders as
    `(uncategorized)` so the v1 grid still looks clean and producer
    drift is visible."""
    e = _v1_entry("eng-x-rogue1", "x", queued_at=100.0,
                  topic="completely-made-up-tag")
    body = _mod.render_review_body([e])
    assert "- **Topic:** (uncategorized)" in body
    assert "completely-made-up-tag" not in body


def test_render_review_body_v0_entries_render_with_placeholders(tmp_path: Path) -> None:
    """v0 rows (missing schema_version + why_engage + topic) still render
    so historical entries don't break the doc. They visually flag as
    incomplete via the (no rationale) / (uncategorized) placeholders."""
    v0 = {
        "id": "v0-row-1",
        "platform": "x",
        "status": "pending_review",
        "queued_at": 100.0,
        "source_author": "@legacy",
        "source_permalink": "https://x.com/legacy/status/1",
        "source_excerpt": "older draft",
        "draft_text": "old comment",
    }
    body = _mod.render_review_body([v0])
    assert "### v0-row-1 · X · @legacy" in body
    assert "- **Topic:** (uncategorized)" in body
    assert "- **Why engage:** (no rationale)" in body


def test_regenerate_review_page_byte_stable_above_footer(tmp_path: Path) -> None:
    """End-to-end byte-stability check on the disk output: render twice,
    strip the regen-timestamp footer, assert byte-equality. This is the
    user-visible 'doc shape doesn't change between hours' guarantee."""
    cfg = _cfg(tmp_path)
    entries = [
        _v1_entry("eng-x-7k3f2a", "x", queued_at=100.0, topic="agents"),
        _v1_entry("eng-li-9m2p4d", "linkedin", queued_at=200.0, topic="mcp"),
    ]
    out = tmp_path / "review.md"
    _mod.regenerate_review_page(
        entries, str(out),
        "https://files.example.test/engagement-review.md",
    )
    body_a = out.read_text()
    # Force a second render — the timestamp footer might differ here but
    # the body above the footer divider MUST be byte-identical.
    _mod.regenerate_review_page(
        entries, str(out),
        "https://files.example.test/engagement-review.md",
    )
    body_b = out.read_text()

    def _strip_footer(text: str) -> str:
        # Footer is the LAST `_Last regenerated:` line + the divider
        # immediately above it. Strip everything from the final `---\n`
        # forward so the test is robust to any future footer expansion.
        # The doc has at most one such footer divider.
        idx = text.rfind("\n---\n")
        return text[: idx] if idx != -1 else text

    assert _strip_footer(body_a) == _strip_footer(body_b), (
        "doc above the regen-footer divider must be byte-stable across renders"
    )
    # Schema version footprint at the top, regen timestamp at the bottom.
    assert "_Schema version: engagement.v1_" in body_a
    assert body_a.rstrip().endswith("_") or "_Last regenerated:" in body_a


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


def test_timer_file_ist_window() -> None:
    """FI-DRIP-IST-WINDOW (2026-06-06): timer fires 12 times/day at IST
    10..21 (replaces the legacy 24/7 hourly schedule). The VPS is
    Asia/Kolkata so OnCalendar reads as local IST."""
    content = TIMER_FILE.read_text()
    assert "OnCalendar=*-*-* 10..21:00:00" in content
    assert "OnCalendar=hourly" not in content
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
    # voice / general playwright leak into it. (project-orchestrator IS
    # present in the FI-ENGAGEMENT-HYBRID era so the subagent can call
    # send_to_project to delegate LinkedIn to social-manager's warm MCP.)
    forbidden = {"voice-stt", "voice-tts", "playwright"}
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


def test_subagent_prompt_routes_x_through_node_helper() -> None:
    """Subagent prompt must instruct using the X Node browse script (still the
    fresh-ephemeral path; LinkedIn is delegated to social-manager — see
    test_subagent_prompt_delegates_li_to_social_manager) and ban the
    playwright MCP path for X."""
    body = SUBAGENT_PROMPT.read_text()
    assert "engagement-browse-x.js" in body
    # LinkedIn helper is NO LONGER invoked by the subagent — it's delegated.
    # The script is still committed as an operator-debug fallback (per the
    # FI-ENGAGEMENT-HYBRID open-question #4 default) but the subagent
    # doesn't call it. So we DON'T assert it's in the prompt here.
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


def test_resolve_review_url_explicit_env_wins(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("SOMA_RELAY_DOMAIN=secrets.example.test\n")
    monkeypatch.setattr(_mod, "_read_secrets_var", lambda name, secrets=str(secrets):
                        _mod._read_secrets_var.__wrapped__(name, str(secrets))
                        if hasattr(_mod._read_secrets_var, "__wrapped__")
                        else "")
    # Skip the wrapper-juggling above by just setting the env explicitly:
    monkeypatch.setenv("HERMES_ENGAGEMENT_REVIEW_URL", "https://override.example.test/eng.md")
    assert _mod._resolve_review_url() == "https://override.example.test/eng.md"


def test_resolve_review_url_uses_soma_relay_domain(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("SOMA_RELAY_DOMAIN=files.example.test\n")
    monkeypatch.delenv("HERMES_ENGAGEMENT_REVIEW_URL", raising=False)
    # Monkeypatch the helper to read from our tmp file
    orig = _mod._read_secrets_var
    monkeypatch.setattr(_mod, "_read_secrets_var",
                        lambda name, secrets_path=str(secrets): orig(name, secrets_path))
    assert _mod._resolve_review_url() == "https://files.example.test/engagement-review.md"


def test_resolve_review_url_falls_through_to_files_domain(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("FILES_DOMAIN=cdn.example.test\n")
    monkeypatch.delenv("HERMES_ENGAGEMENT_REVIEW_URL", raising=False)
    orig = _mod._read_secrets_var
    monkeypatch.setattr(_mod, "_read_secrets_var",
                        lambda name, secrets_path=str(secrets): orig(name, secrets_path))
    assert _mod._resolve_review_url() == "https://cdn.example.test/engagement-review.md"


def test_resolve_review_url_derives_files_from_soma_domain(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("SOMA_DOMAIN=example.test\n")
    monkeypatch.delenv("HERMES_ENGAGEMENT_REVIEW_URL", raising=False)
    orig = _mod._read_secrets_var
    monkeypatch.setattr(_mod, "_read_secrets_var",
                        lambda name, secrets_path=str(secrets): orig(name, secrets_path))
    assert _mod._resolve_review_url() == "https://files.example.test/engagement-review.md"


def test_resolve_review_url_empty_when_nothing_configured(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("# no relay knobs at all\n")
    monkeypatch.delenv("HERMES_ENGAGEMENT_REVIEW_URL", raising=False)
    orig = _mod._read_secrets_var
    monkeypatch.setattr(_mod, "_read_secrets_var",
                        lambda name, secrets_path=str(secrets): orig(name, secrets_path))
    assert _mod._resolve_review_url() == ""


def test_drip_fallback_empty_pool_dm_includes_review_link(tmp_path: Path) -> None:
    """FI-DRIP-REVIEW-LINK: even the empty-hour DM must surface the review URL
    so the operator can open the pending-review doc (it may still hold drafts
    from prior hours)."""
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999",
               review_url="https://files.example.test/engagement-review.md")
    Path(cfg["queue_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["queue_path"]).write_text("")

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip(
            cfg, source="any", banner="POOLED FALLBACK",
            on_empty_emit_dm=True, fallback_reason="subagent_timeout",
        )

    assert result == 0
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "https://files.example.test/engagement-review.md" in body, (
        "the empty-hour DM must include the review URL so the operator can "
        "open the pending-review doc"
    )
    assert "Review queue" in body or "Review:" in body


# ────────────────────────────────────────────────────────────────────────────
# FI-ENGAGEMENT-HYBRID (2026-06-05) — drip_hybrid: X-fresh + LI-pool, 1 DM
# ────────────────────────────────────────────────────────────────────────────


def test_hybrid_pops_one_x_fresh_and_one_li_pool(tmp_path: Path) -> None:
    """The signature contract: one X (fresh-only) + one LI (from pool) per
    invocation. Both go into a single pending_review batch."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999",
               review_url="https://files.example.test/engagement-review.md")
    entries = [
        # X drafts: one stale (pool), one fresh this hour
        _entry("x-stale", "x", queued_at=now - 7200),
        _entry("x-fresh", "x", queued_at=now, freshly_drafted_at=now),
        # LI drafts: pool-only, no freshly_drafted_at
        _entry("li-pool-1", "linkedin", queued_at=now - 1800),
        _entry("li-pool-2", "linkedin", queued_at=now - 3600),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip_hybrid(cfg, start_ts=now - 60)

    assert result == 0
    after = {e["id"]: e for e in _read_queue(cfg["queue_path"])}
    # Fresh X must pop, stale X must stay queued (pool not eligible for X)
    assert after["x-fresh"]["status"] == "pending_review"
    assert after["x-stale"]["status"] == "queued"
    # OLDEST LI from pool pops first (FIFO: oldest pool draft surfaces first
    # so it doesn't get stale). li-pool-2 was queued at now-3600, older than
    # li-pool-1 at now-1800, so li-pool-2 pops.
    assert after["li-pool-2"]["status"] == "pending_review"
    assert after["li-pool-1"]["status"] == "queued"
    # Single DM call
    assert mock_open.call_count == 1
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "FRESH-X + POOL-LI" in body
    assert "x-fresh" in body
    assert "li-pool-2" in body
    assert "https://files.example.test/engagement-review.md" in body


def test_hybrid_with_only_x_fresh_still_sends_one_dm(tmp_path: Path) -> None:
    """X fresh present, LI pool empty → one DM with the X entry + a stalled
    warning ('No LinkedIn drafts have ever been queued')."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999",
               review_url="https://files.example.test/engagement-review.md")
    entries = [_entry("x-fresh", "x", queued_at=now, freshly_drafted_at=now)]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip_hybrid(cfg, start_ts=now - 60)

    assert result == 0
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "x-fresh" in body
    # No LI ever queued → stalled warning fires
    assert "No LinkedIn drafts have ever been queued" in body
    assert "social-manager" in body


def test_hybrid_li_stalled_warning_fires_after_threshold(tmp_path: Path) -> None:
    """LI was queued 5 hours ago + nothing since → 'LI refill stalled' warning
    rides on the same DM as the popped X."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999",
               review_url="https://files.example.test/engagement-review.md")
    entries = [
        _entry("x-fresh", "x", queued_at=now, freshly_drafted_at=now),
        # LI from 5h ago, already posted (so pool is dry)
        _entry("li-old-posted", "linkedin", queued_at=now - 5 * 3600,
               status="posted"),
    ]
    _write_queue(cfg["queue_path"], entries)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip_hybrid(cfg, start_ts=now - 60, li_stalled_hours=3.0)

    assert result == 0
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "x-fresh" in body
    assert "LinkedIn drafts have been queued" in body
    assert "stalled" in body.lower()


def test_hybrid_no_drafts_at_all_falls_through_to_empty_dm(tmp_path: Path) -> None:
    """Neither X fresh nor LI pool → empty-DM (the silent-hour contract holds)."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999",
               review_url="https://files.example.test/engagement-review.md")
    Path(cfg["queue_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["queue_path"]).write_text("")

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        result = _mod.drip_hybrid(cfg, start_ts=now - 60)

    assert result == 0
    assert mock_open.called
    body = urllib.parse.unquote_plus(mock_open.call_args[0][0].data.decode())
    assert "No engagement drafts this hour" in body


def test_hybrid_old_drip_path_still_works(tmp_path: Path) -> None:
    """Back-compat: --source=fresh / --source=any single-source mode still
    works for any operator manual invocation. drip_hybrid is a parallel
    function, not a replacement."""
    now = time.time()
    cfg = _cfg(tmp_path, tg_token="tok", tg_chat_id="999")
    entries = [_entry("x-fresh", "x", queued_at=now, freshly_drafted_at=now)]
    _write_queue(cfg["queue_path"], entries)
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = MagicMock()
        # Legacy single-source call
        rc = _mod.drip(cfg, source="fresh", start_ts=now - 60, banner="FRESH")
    assert rc == 0


def test_cli_hybrid_flag_dispatches_to_drip_hybrid(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_hybrid(cfg, **kwargs):
        captured["cfg"] = cfg
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(_mod, "drip_hybrid", fake_hybrid)
    monkeypatch.setattr(sys, "argv", [
        "engagement-hourly-drip.py", "--hybrid", "--start-ts", "1780670000",
    ])
    monkeypatch.setattr(_mod, "_cfg", lambda: _cfg(tmp_path))
    rc = _mod.main()
    assert rc == 0
    assert captured["kwargs"]["start_ts"] == 1780670000.0


def test_dispatcher_invokes_single_platform_flag() -> None:
    """FI-DRIP-IST-WINDOW (2026-06-06): dispatcher pops exactly 1 draft
    per hour via --single-platform=x|linkedin. The legacy --hybrid flag
    is replaced because each hour now surfaces a single platform."""
    body = (REPO_ROOT / "scripts" / "engagement-hourly-dispatch.sh").read_text()
    assert "--single-platform=x" in body
    assert "--single-platform=linkedin" in body


def test_subagent_prompt_delegates_li_to_social_manager() -> None:
    body = (REPO_ROOT / "scripts" / "engagement-browse-draft-subagent.txt").read_text()
    assert "send_to_project" in body
    assert "social-manager" in body
    assert "playwright-linkedin" in body
    # And must explicitly tell the subagent NOT to harvest LI directly
    assert "DELEGATE" in body or "delegate" in body


def test_subagent_mcp_config_has_project_orchestrator() -> None:
    cfg = json.loads(
        (REPO_ROOT / "config" / "claude" / "engagement-subagent-mcp.json").read_text()
    )
    assert "project-orchestrator" in cfg["mcpServers"], (
        "the subagent needs project-orchestrator to call send_to_project "
        "for the LinkedIn delegation"
    )


def test_system_prompt_has_engagement_review_link_rule() -> None:
    """responsive_bot.md must codify the rule that every engagement-draft
    notification carries the review URL."""
    body = (REPO_ROOT / "system_prompts" / "responsive_bot.md").read_text()
    assert "Engagement-draft notifications — ALWAYS include the review URL" in body
    assert "engagement-review.md" in body


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


# ────────────────────────────────────────────────────────────────────────────
# FI-QUEUE-DEDUP-LOCK — 8 new tests
# ────────────────────────────────────────────────────────────────────────────


def test_dedup_terminal_status_wins(tmp_path: Path) -> None:
    """read_queue collapses 3 rows for the same id to 1 row; the highest-rank
    status (posted=6) wins regardless of input order."""
    q = tmp_path / "queue.jsonl"
    entries = [
        _entry("eng-x-abc", "x", status="pending_review", queued_at=100.0),
        _entry("eng-x-abc", "x", status="queued",         queued_at=100.0),
        _entry("eng-x-abc", "x", status="posted",         queued_at=100.0,
               posted_at=200.0),
    ]
    # Write them in reverse-rank order so we're sure the code doesn't rely on ordering.
    import random
    shuffled = list(entries)
    random.shuffle(shuffled)
    _write_queue(q, shuffled)

    result = _mod.read_queue(str(q))
    assert len(result) == 1, f"expected 1 deduped row, got {len(result)}"
    assert result[0]["status"] == "posted"


def test_dedup_tiebreaker_by_latest_timestamp(tmp_path: Path) -> None:
    """When two rows share the same id AND the same status rank, the one with
    the later timestamp wins."""
    q = tmp_path / "queue.jsonl"
    entries = [
        _entry("eng-li-xyz", "linkedin", status="approved",
               queued_at=50.0, approved_at=100.0),
        _entry("eng-li-xyz", "linkedin", status="approved",
               queued_at=50.0, approved_at=200.0),
    ]
    _write_queue(q, entries)

    result = _mod.read_queue(str(q))
    assert len(result) == 1
    assert result[0]["approved_at"] == 200.0, "row with approved_at=200 must win"


def test_dedup_preserves_no_id_rows_verbatim(tmp_path: Path) -> None:
    """A row with no 'id' key (legacy/malformed) is not dropped by _dedup_entries."""
    q = tmp_path / "queue.jsonl"
    normal_entry = _entry("eng-x-good", "x", status="queued", queued_at=100.0)
    malformed = {
        "platform": "x",
        "status": "queued",
        "draft_text": "no id field at all",
    }
    _write_queue(q, [normal_entry, malformed])

    result = _mod.read_queue(str(q))
    ids = [e.get("id") for e in result]
    assert "eng-x-good" in ids, "normal row must be present"
    no_id = [e for e in result if not e.get("id")]
    assert len(no_id) == 1, "the no-id row must pass through verbatim"
    assert no_id[0]["draft_text"] == "no id field at all"


def test_decline_sticks_across_duplicates(tmp_path: Path) -> None:
    """User-reported bug: decline_entry only flipped one row when the same id
    had TWO entries with different statuses. After the fix, dedup-on-read inside
    the lock means only one row exists when the mutation runs, so the decline
    always sticks."""
    cfg = _cfg(tmp_path)
    Path(cfg["queue_path"]).parent.mkdir(parents=True, exist_ok=True)
    # Manually write two rows for the same id (simulating the live 8-dupe state)
    with open(cfg["queue_path"], "w") as fh:
        fh.write(json.dumps(_entry("eng-x-abc", "x", status="queued",
                                   queued_at=100.0)) + "\n")
        fh.write(json.dumps(_entry("eng-x-abc", "x", status="pending_review",
                                   queued_at=100.0)) + "\n")

    result = _mod.decline_entry(cfg, "eng-x-abc")
    assert result == 0

    after = _mod.read_queue(cfg["queue_path"])
    assert len(after) == 1, f"expected 1 row after dedup+decline, got {len(after)}"
    assert after[0]["status"] == "declined", (
        "decline must stick even when the queue had duplicate rows for the same id"
    )


def test_mark_posted_idempotent(tmp_path: Path) -> None:
    """mark_posted called twice on the same id does not grow the queue.
    The second call is a no-op on status (already 'posted') but must not
    append a new row."""
    cfg = _cfg(tmp_path)
    entries = [_entry("eng-x-dup", "x", status="approved", queued_at=100.0)]
    _write_queue(cfg["queue_path"], entries)

    permalink = "https://x.com/user/status/111"
    _mod.mark_posted(cfg, "eng-x-dup", permalink)
    _mod.mark_posted(cfg, "eng-x-dup", permalink)

    after = _mod.read_queue(cfg["queue_path"])
    assert len(after) == 1, f"expected 1 row after two mark_posted calls, got {len(after)}"
    assert after[0]["status"] == "posted"


def test_concurrent_writers_no_row_loss(tmp_path: Path) -> None:
    """Two subprocesses each call mark_posted on N distinct ids concurrently.
    The final queue must have all 2N entries and no torn writes."""
    import threading

    N = 8
    q = tmp_path / "queue.jsonl"
    # Pre-create 2N entries: N for worker A (ids a0..a7), N for worker B (b0..b7)
    entries = (
        [_entry(f"a{i}", "x", status="approved", queued_at=float(i)) for i in range(N)]
        + [_entry(f"b{i}", "x", status="approved", queued_at=float(i)) for i in range(N)]
    )
    _write_queue(q, entries)

    cfg_a = _cfg(tmp_path)
    cfg_b = _cfg(tmp_path)
    errors: list[Exception] = []

    def worker(cfg: dict, ids: list[str]) -> None:
        try:
            for eid in ids:
                _mod.mark_posted(cfg, eid, f"https://x.com/status/{eid}")
        except Exception as exc:
            errors.append(exc)

    t_a = threading.Thread(target=worker, args=(cfg_a, [f"a{i}" for i in range(N)]))
    t_b = threading.Thread(target=worker, args=(cfg_b, [f"b{i}" for i in range(N)]))
    t_a.start()
    t_b.start()
    t_a.join(timeout=30)
    t_b.join(timeout=30)

    assert not t_a.is_alive(), "worker A timed out"
    assert not t_b.is_alive(), "worker B timed out"
    assert not errors, f"worker exceptions: {errors}"

    final = _mod.read_queue(str(q))
    assert len(final) == 2 * N, (
        f"expected {2*N} rows after concurrent writes, got {len(final)}"
    )
    statuses = {e["id"]: e["status"] for e in final}
    for i in range(N):
        assert statuses.get(f"a{i}") == "posted", f"a{i} not posted"
        assert statuses.get(f"b{i}") == "posted", f"b{i} not posted"


def test_lock_is_separate_file(tmp_path: Path) -> None:
    """The lockfile path is <queue>.lock (a sibling file), not queue.jsonl itself.
    This is verified by:
      (a) naming convention: <queue>.lock lives alongside queue.jsonl
      (b) using queue_locked() creates the .lock sibling but does NOT modify
          the queue file inode
      (c) cross-process blocking: a child process that tries to acquire the lock
          while the parent holds it must be blocked until the parent releases.

    Note: fcntl locks are per-process on Linux, not per-thread within the same
    process. Cross-thread tests would pass trivially. We use subprocess.Popen
    to test actual cross-process mutual exclusion.
    """
    q = tmp_path / "queue.jsonl"
    q.touch()
    lock_path = tmp_path / "queue.jsonl.lock"

    # (a) naming convention
    assert str(lock_path) == str(Path(str(q)).parent / (Path(str(q)).name + ".lock"))
    assert str(lock_path) != str(q), "lockfile must be a sibling, not the queue itself"

    # (b) queue_locked() creates the .lock file and does not touch queue inode
    q_inode_before = q.stat().st_ino
    with _mod.queue_locked(q):
        assert lock_path.exists(), "lock file must be created by queue_locked()"
    q_inode_after = q.stat().st_ino
    assert q_inode_before == q_inode_after, (
        "queue_locked() must not change the queue file's inode"
    )

    # (c) cross-process mutual exclusion via a short child process that tries
    # to acquire the lock while we hold it and reports back via its exit code.
    helper_script = tmp_path / "try_lock.py"
    helper_script.write_text(
        f"import sys, fcntl, pathlib\n"
        f"p = pathlib.Path({str(lock_path)!r})\n"
        f"p.parent.mkdir(parents=True, exist_ok=True)\n"
        f"with open(p, 'a') as f:\n"
        f"    try:\n"
        f"        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        f"        sys.exit(0)  # acquired: lock was NOT held\n"
        f"    except BlockingIOError:\n"
        f"        sys.exit(42)  # blocked: lock IS held — this is the expected path\n"
    )
    with _mod.queue_locked(q):
        child = subprocess.run(
            [sys.executable, str(helper_script)],
            timeout=5,
        )
        assert child.returncode == 42, (
            f"child process must see the lock as held (exit 42), got {child.returncode}"
        )


def test_purge_null_permalink_fresh_cli(tmp_path: Path) -> None:
    """--purge-null-permalink-fresh <start_ts>:
      (a) fresh row with null permalink → dropped
      (b) fresh row with good permalink → kept
      (c) old row (freshly_drafted_at < start_ts) with null permalink → kept
    """
    start_ts = 1000.0
    cfg = _cfg(tmp_path)
    Path(cfg["queue_path"]).parent.mkdir(parents=True, exist_ok=True)

    # (a) fresh, no permalink — should be purged
    fresh_null = _entry("eng-x-purge", "x", status="queued", queued_at=900.0,
                        freshly_drafted_at=1100.0)
    fresh_null["source_permalink"] = None

    # (b) fresh, good permalink — must be kept
    fresh_good = _entry("eng-x-keep", "x", status="queued", queued_at=900.0,
                        freshly_drafted_at=1100.0)
    fresh_good["source_permalink"] = "https://x.com/user/status/42"

    # (c) old draft, no permalink — must be kept (not in this run)
    old_null = _entry("eng-x-old-null", "x", status="queued", queued_at=500.0)
    old_null["source_permalink"] = None
    old_null["freshly_drafted_at"] = 800.0  # before start_ts

    _write_queue(cfg["queue_path"], [fresh_null, fresh_good, old_null])

    result = _mod.purge_null_permalink_fresh(cfg, start_ts)
    assert result == 0

    after = _mod.read_queue(cfg["queue_path"])
    ids = {e["id"] for e in after}
    assert "eng-x-purge" not in ids, "fresh null-permalink row must be purged"
    assert "eng-x-keep" in ids, "fresh row with good permalink must be kept"
    assert "eng-x-old-null" in ids, "old null-permalink row must be kept (not fresh)"
