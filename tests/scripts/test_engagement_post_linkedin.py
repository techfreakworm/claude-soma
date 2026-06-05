"""Tests for scripts/engagement-post-linkedin.js (FI-LI-POST-AUTHFAIL).

Static checks only — the script drives a live LinkedIn session that can't be
mocked, so the contracts we lock in are:

  - The /feed/ warm-up is wired in (the prior version skipped this and
    returned RESULT:AUTHFAIL on every draft because of guest-UI rendering).
  - NEEDS_REAUTH replaces the legacy AUTHFAIL label so callers see a
    distinct, actionable signal instead of the ambiguous old name.
  - UNREACHABLE is genuinely separate from NEEDS_REAUTH (post unavailable
    to viewer != auth expired).
  - The known-good submit-button selector
    `button[class*="comments-comment-box__submit-button"]` is preserved
    (confirmed still live 2026-06-05 via diag probe — class
    `comments-comment-box__submit-button--cr`).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "engagement-post-linkedin.js"
BROWSE_SCRIPT = REPO_ROOT / "scripts" / "engagement-browse-linkedin.js"


def test_post_script_warms_up_feed() -> None:
    body = SCRIPT.read_text()
    assert "linkedin.com/feed/" in body
    assert "warm" in body.lower(), (
        "post script must warm up /feed/ before navigating to urn permalink — "
        "without this, LinkedIn renders guest UI even with valid cookies"
    )


def test_post_script_uses_needs_reauth_label() -> None:
    body = SCRIPT.read_text()
    assert "RESULT:NEEDS_REAUTH" in body, (
        "the legacy RESULT:AUTHFAIL label must be replaced with NEEDS_REAUTH "
        "so callers see a clean, actionable signal"
    )


def test_post_script_distinguishes_reauth_from_unreachable() -> None:
    """A post that's removed / private to viewer must label UNREACHABLE,
    not NEEDS_REAUTH — they need different operator responses."""
    body = SCRIPT.read_text()
    assert "RESULT:UNREACHABLE" in body
    assert "This post is unavailable" in body or "unavailable" in body.lower()


def test_post_script_uses_authwall_url_signal() -> None:
    """Final URL pointing at /authwall or /checkpoint is the most reliable
    NEEDS_REAUTH signal (more reliable than head-text matching)."""
    body = SCRIPT.read_text()
    assert "authwall" in body or "checkpoint" in body


def test_post_script_preserves_submit_selector() -> None:
    """The submit button class `comments-comment-box__submit-button` (with
    --cr Lighthouse suffix) is the only durable selector. Diag 2026-06-05
    confirmed it's still live."""
    body = SCRIPT.read_text()
    assert "comments-comment-box__submit-button" in body, (
        "do not rename the submit-button selector — the class-contains match "
        "is what survives LinkedIn's --cr Lighthouse suffix rotation"
    )


def test_post_script_preserves_quill_editor_selector() -> None:
    """`div.ql-editor[contenteditable="true"]` confirmed still live."""
    body = SCRIPT.read_text()
    assert 'div.ql-editor[contenteditable="true"]' in body


def test_browse_script_extracts_real_urn_permalinks() -> None:
    """browse must NOT emit /in/<slug>/recent-activity/all/ URLs as permalinks
    — those bounce to authwall on direct navigation. It must extract
    urn:li:activity IDs from card innerHTML and emit
    /feed/update/urn:li:activity:<id>/ URLs."""
    body = BROWSE_SCRIPT.read_text()
    assert "urn:li:activity" in body
    assert "feed/update/" in body
    # And the prior broken pattern must NOT be the active permalink format
    assert "/recent-activity/all/" not in body, (
        "remove the /recent-activity/all/ permalink format — those URLs "
        "redirect to authwall and post_li.js correctly rejects them as "
        "NEEDS_REAUTH"
    )


def test_browse_script_skips_cards_without_urn() -> None:
    """Cards without a urn (promoted-page injections, suggested content,
    LinkedIn Learning) should be skipped rather than fabricated."""
    body = BROWSE_SCRIPT.read_text()
    # The `continue` statement inside the per-card loop is the skip mechanism
    assert "continue" in body
    assert "urnMatch" in body or "urn:li:activity" in body


# ────────────────────────────────────────────────────────────────────────────
# FI-NO-POST-WITHOUT-APPROVAL (2026-06-05): the post helpers MUST default to
# dry-run + only click submit when explicitly approved. The bot / subagent
# cannot set the approval flag itself.
# ────────────────────────────────────────────────────────────────────────────


def test_li_post_script_dry_run_by_default() -> None:
    body = SCRIPT.read_text()
    assert "DRY_RUN-READY" in body, (
        "post_li.js must emit RESULT:DRY_RUN-READY by default"
    )
    assert "--i-have-user-approval" in body, (
        "post_li.js must require --i-have-user-approval (or "
        "HERMES_POST_APPROVAL=yes) to actually submit"
    )
    assert "HERMES_POST_APPROVAL" in body
    # And the approval flag must gate the submit click — assert the deny
    # branch precedes the click()
    pre_click_section = body.split("await post.click()")[0]
    assert "!APPROVED" in pre_click_section, (
        "the !APPROVED dry-run branch must run BEFORE the submit click"
    )


def test_x_post_script_dry_run_by_default() -> None:
    x_script = REPO_ROOT / "scripts" / "engagement-post-x.js"
    body = x_script.read_text()
    assert "DRY_RUN-READY" in body
    assert "--i-have-user-approval" in body
    assert "HERMES_POST_APPROVAL" in body
    pre_click_section = body.split("await btn.click()")[0]
    assert "!APPROVED" in pre_click_section, (
        "the !APPROVED dry-run branch must run BEFORE the submit click"
    )


def test_post_scripts_help_text_mentions_dry_run_default() -> None:
    """The usage message must tell the caller what the default is."""
    for name in ("engagement-post-linkedin.js", "engagement-post-x.js"):
        body = (REPO_ROOT / "scripts" / name).read_text()
        # Usage block must mention dry-run + the approval flag
        assert "DEFAULT MODE: dry-run" in body, (
            f"{name} usage must surface the dry-run default"
        )


def test_system_prompt_has_absolute_hard_rule_block() -> None:
    """responsive_bot.md must carry the #1-priority rule at the top."""
    body = (REPO_ROOT / "system_prompts" / "responsive_bot.md").read_text()
    assert "ABSOLUTE HARD RULE — outbound public actions require explicit per-action approval" in body
    # And the cross-reference in Hard prohibitions
    assert "NEVER take an outbound public action without explicit per-action" in body
