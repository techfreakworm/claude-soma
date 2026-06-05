"""Tests for scripts/engagement-posted.sh — FI-REVIEW-DOC-BELT-AND-SUSPENDERS.

The wrapper is the entry point that ALL posting agents must use after
calling the raw post helpers, so it has to:

  1. Mark the queue entry posted (or failed) via engagement-hourly-drip.py.
  2. ALWAYS re-publish the engagement review doc afterward, even if the
     inner mark step's implicit regen quietly errored — that is the
     belt-and-suspenders semantics the user demanded after the
     "still shows posted items" stale-doc incident.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "engagement-posted.sh"


def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file()
    assert os.access(str(SCRIPT), os.X_OK)


def test_bash_syntax() -> None:
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def test_script_explicitly_invokes_regen_only() -> None:
    """The whole point of FI-REVIEW-DOC-BELT-AND-SUSPENDERS is that the
    wrapper makes a SEPARATE --regen-only call after the mark step.
    A static check catches accidental removal."""
    body = SCRIPT.read_text()
    assert "--regen-only" in body, (
        "engagement-posted.sh must explicitly call --regen-only after the "
        "status change — that is the FI-REVIEW-DOC-BELT-AND-SUSPENDERS fix"
    )
    # And it must not be the ONLY call (we still mark via --posted/--posted-error).
    assert "--posted" in body
    # exec would replace the shell before the regen call could run.
    assert "exec python3" not in body, (
        "exec replaces the shell process; the post-mark --regen-only call "
        "would never fire. Use regular invocations + explicit exit code."
    )


def test_marks_posted_and_regenerates_review_doc(tmp_path: Path) -> None:
    """End-to-end: feed the wrapper a queue + review doc env and verify the
    queue entry flips to posted AND the review doc is rewritten."""
    queue = tmp_path / "queue.jsonl"
    review = tmp_path / "engagement-review.md"
    log = tmp_path / "drip.log"

    queue.write_text(json.dumps({
        "id": "eng-x-posted-1",
        "platform": "x",
        "status": "approved",
        "source_author": "@alice",
        "source_permalink": "https://x.com/alice/status/1",
        "source_post_excerpt": "an interesting tweet",
        "draft_text": "thoughtful reply",
    }) + "\n")

    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_QUEUE": str(queue),
        "HERMES_ENGAGEMENT_REVIEW_PAGE": str(review),
        "HERMES_ENGAGEMENT_LOG": str(log),
        "HERMES_ENGAGEMENT_REVIEW_URL": "https://files.example.test/engagement-review.md",
    }

    r = subprocess.run(
        ["bash", str(SCRIPT), "eng-x-posted-1", "https://x.com/me/status/42"],
        env=env, capture_output=True, text=True, cwd=str(tmp_path)
    )
    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"

    rows = [json.loads(l) for l in queue.read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["status"] == "posted"
    assert rows[0]["post_permalink"] == "https://x.com/me/status/42"

    # FI-REVIEW-DOC-BELT-AND-SUSPENDERS: the review doc must exist.
    assert review.is_file(), "engagement-posted.sh must regenerate the review doc"


def test_regen_runs_even_if_review_doc_deleted_between_calls(tmp_path: Path) -> None:
    """The explicit --regen-only call exists precisely to recover from
    the case where the inner mark step's regen quietly fails or the doc
    is deleted between the two calls."""
    queue = tmp_path / "queue.jsonl"
    review = tmp_path / "engagement-review.md"
    log = tmp_path / "drip.log"

    queue.write_text(json.dumps({
        "id": "eng-x-belt-1",
        "platform": "x",
        "status": "approved",
        "source_author": "@bob",
        "source_permalink": "https://x.com/bob/status/2",
        "source_post_excerpt": "another tweet",
        "draft_text": "reply",
    }) + "\n")

    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_QUEUE": str(queue),
        "HERMES_ENGAGEMENT_REVIEW_PAGE": str(review),
        "HERMES_ENGAGEMENT_LOG": str(log),
        "HERMES_ENGAGEMENT_REVIEW_URL": "https://files.example.test/x.md",
    }

    r = subprocess.run(
        ["bash", str(SCRIPT), "eng-x-belt-1", "https://x.com/me/status/99"],
        env=env, capture_output=True, text=True, cwd=str(tmp_path)
    )
    assert r.returncode == 0
    assert review.is_file()

    # Capture mtime, delete, re-run --regen-only via the wrapper-equivalent path.
    # (engagement-posted.sh runs --regen-only unconditionally after status,
    # but this second pass tests that --regen-only ALONE — the entrypoint
    # the wrapper depends on — still works.)
    body_v1 = review.read_text()
    review.unlink()
    drip = REPO_ROOT / "scripts" / "engagement-hourly-drip.py"
    r2 = subprocess.run(
        ["python3", str(drip), "--regen-only"],
        env=env, capture_output=True, text=True, cwd=str(tmp_path)
    )
    assert r2.returncode == 0, f"stderr: {r2.stderr}"
    assert review.is_file()
    # Same content (the queue didn't change between calls).
    assert review.read_text() == body_v1


def test_error_path_marks_failed_and_regenerates(tmp_path: Path) -> None:
    """The --error variant must also leave a fresh review doc behind."""
    queue = tmp_path / "queue.jsonl"
    review = tmp_path / "engagement-review.md"
    log = tmp_path / "drip.log"

    queue.write_text(json.dumps({
        "id": "eng-li-fail-1",
        "platform": "linkedin",
        "status": "approved",
        "source_author": "Carol",
        "source_permalink": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
        "source_post_excerpt": "post",
        "draft_text": "comment",
    }) + "\n")

    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_QUEUE": str(queue),
        "HERMES_ENGAGEMENT_REVIEW_PAGE": str(review),
        "HERMES_ENGAGEMENT_LOG": str(log),
        "HERMES_ENGAGEMENT_REVIEW_URL": "https://files.example.test/li.md",
    }

    r = subprocess.run(
        ["bash", str(SCRIPT), "eng-li-fail-1", "--error", "captcha"],
        env=env, capture_output=True, text=True, cwd=str(tmp_path)
    )
    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"

    rows = [json.loads(l) for l in queue.read_text().splitlines() if l.strip()]
    assert rows[0]["status"] == "failed"
    assert rows[0]["post_error"] == "captcha"
    assert review.is_file()
