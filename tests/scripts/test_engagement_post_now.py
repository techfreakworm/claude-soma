"""Tests for scripts/engagement-post-now.sh (FI-POST-STATUS-WRAPPER).

The wrapper drives the real post helpers + the status-update flow, so we
test it on static + simulated-state contracts only — the actual posting
path can't be mocked without a live LinkedIn/X session.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "engagement-post-now.sh"


def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file()
    assert os.access(str(SCRIPT), os.X_OK)


def test_bash_syntax() -> None:
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def test_refuses_without_approval_flag(tmp_path: Path) -> None:
    """Wrapper must refuse to do anything without --i-have-user-approval
    or HERMES_POST_APPROVAL=yes (mirrors FI-NO-POST-WITHOUT-APPROVAL)."""
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "id": "test-1", "platform": "x", "status": "approved",
        "source_permalink": "https://x.com/me/status/1", "draft_text": "hi"
    }) + "\n")
    env = {**os.environ, "HERMES_ENGAGEMENT_QUEUE": str(queue)}
    env.pop("HERMES_POST_APPROVAL", None)
    r = subprocess.run(
        ["bash", str(SCRIPT), "test-1"],
        env=env, capture_output=True, text=True
    )
    assert r.returncode == 2
    assert "refusing to post" in r.stderr.lower()


def test_refuses_unknown_id(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    queue.write_text("")
    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_QUEUE": str(queue),
        "HERMES_ENGAGEMENT_LOG": str(tmp_path / "drip.log"),
    }
    r = subprocess.run(
        ["bash", str(SCRIPT), "no-such-id", "--i-have-user-approval"],
        env=env, capture_output=True, text=True
    )
    assert r.returncode == 3
    assert "not found" in r.stderr.lower()


def test_refuses_draft_with_no_permalink(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "id": "test-noperm", "platform": "x", "status": "approved",
        "source_permalink": "", "draft_text": "x"
    }) + "\n")
    env = {
        **os.environ,
        "HERMES_ENGAGEMENT_QUEUE": str(queue),
        "HERMES_ENGAGEMENT_LOG": str(tmp_path / "drip.log"),
    }
    r = subprocess.run(
        ["bash", str(SCRIPT), "test-noperm", "--i-have-user-approval"],
        env=env, capture_output=True, text=True
    )
    assert r.returncode == 3
    assert "no source_permalink" in r.stderr.lower() or "un-postable" in r.stderr.lower()


def test_help_text_mentions_approval_flag_and_loop_closure() -> None:
    """Header docstring must surface both the approval requirement AND
    the auto-status-update behavior — operators rely on it."""
    body = SCRIPT.read_text()
    assert "--i-have-user-approval" in body
    assert "engagement-posted.sh" in body or "--posted" in body
    assert "awaiting post" in body.lower(), (
        "the stale-doc incident this wrapper exists to fix must be "
        "named in the docstring so the next operator understands the why"
    )


def test_responsive_bot_prompt_points_to_wrapper() -> None:
    body = (REPO_ROOT / "system_prompts" / "responsive_bot.md").read_text()
    assert "engagement-post-now.sh" in body
    # And the cross-reference must say "never the raw helpers"
    assert "go through engagement-post-now.sh" in body or "Never the raw helpers" in body.lower()
