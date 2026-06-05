"""Tests for scripts/relay_link_gate.py (FI-RELAY-LINK-GATE).

The hook reads a Claude Code PreToolUse event JSON from stdin and writes
its decision JSON to stdout. Each test crafts an event payload + asserts
on the parsed stdout.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "relay_link_gate.py"


def _run(event: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


def _send_reply_event(text: str, tool: str = "mcp__hermes_api__send_tg_reply") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "test-sid",
        "tool_name": tool,
        "tool_input": {"text": text, "chat_id": "1"},
    }


# ── allow cases ────────────────────────────────────────────────────────────


def test_short_message_allowed() -> None:
    assert _run(_send_reply_event("Build is green.")) == {}


def test_short_with_link_allowed() -> None:
    body = (
        "Audit done — 4 P1s, 2 P2s.\n\n"
        "Full doc: https://files.example.test/general/audit-2026-06-05.md"
    )
    assert _run(_send_reply_event(body)) == {}


def test_one_short_fenced_code_allowed() -> None:
    body = (
        "Run this:\n"
        "```bash\n"
        "sudo systemctl restart claude-soma-engagement-drip.timer\n"
        "sudo systemctl status claude-soma-engagement-drip.timer\n"
        "```\n"
        "Then watch the dispatch log."
    )
    assert _run(_send_reply_event(body)) == {}


def test_single_heading_allowed() -> None:
    body = (
        "## Status\n\n"
        "Deploy green. Two ticks in fresh-on-X. LinkedIn still pending push."
    )
    assert _run(_send_reply_event(body)) == {}


def test_non_target_tool_ignored() -> None:
    # Long body via a tool we don't gate
    long = "x" * 5000
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "test-sid",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/big.txt"},
    }
    assert _run(event) == {}


def test_non_pretooluse_ignored() -> None:
    event = _send_reply_event("x" * 5000)
    event["hook_event_name"] = "PostToolUse"
    assert _run(event) == {}


def test_bypass_marker_allows_any_length() -> None:
    body = (
        "[RELAY-INLINE-OK]\n\n"
        "## a\n## b\n## c\n\n"
        "```python\n" + ("line\n" * 100) + "```\n" + ("x" * 5000)
    )
    assert _run(_send_reply_event(body)) == {}


# ── deny cases ──────────────────────────────────────────────────────────────


def test_long_body_denied() -> None:
    out = _run(_send_reply_event("lorem ipsum " * 200))  # ~2400 chars
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "body length" in reason
    assert "soma-relay publish" in reason  # the actionable remediation


def test_long_fenced_code_block_denied() -> None:
    body = "Run:\n```python\n" + "\n".join(f"line_{i}" for i in range(20)) + "\n```\n"
    out = _run(_send_reply_event(body))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fenced code block" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_multi_heading_doc_denied() -> None:
    body = (
        "Audit\n\n"
        "## Findings\n\nThings.\n\n"
        "## Recommendations\n\nMore things.\n\n"
        "## Next steps\n\nShip."
    )
    out = _run(_send_reply_event(body))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "markdown section headings" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_plugin_telegram_reply_also_gated() -> None:
    out = _run(
        _send_reply_event(
            "lorem ipsum " * 200,
            tool="mcp__plugin_telegram_telegram__reply",
        )
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_reason_quotes_workflow_steps() -> None:
    """The deny reason must walk the model through write-to-file → publish
    → resend; otherwise the model retries blind."""
    out = _run(_send_reply_event("x" * 2000))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "soma-relay publish" in reason
    assert "Re-sent as a SHORT Telegram message" in reason or "SHORT" in reason
    assert "[RELAY-INLINE-OK]" in reason  # bypass escape hatch documented


def test_hook_registered_in_hooks_json() -> None:
    cfg = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())
    pre_tool = cfg["hooks"]["PreToolUse"]
    send_reply_groups = [
        g for g in pre_tool
        if "send_tg_reply" in g.get("matcher", "")
        or "telegram__reply" in g.get("matcher", "")
    ]
    assert send_reply_groups, "no PreToolUse matcher group for send_tg_reply"
    commands = [h.get("command", "") for g in send_reply_groups for h in g.get("hooks", [])]
    assert any("relay_link_gate.py" in c for c in commands), (
        "relay_link_gate.py must be registered alongside heard_gate.py"
    )
    assert any("heard_gate.py" in c for c in commands), (
        "heard_gate.py must remain registered (regression guard)"
    )


def test_system_prompt_has_relay_link_gate_block() -> None:
    body = (REPO_ROOT / "system_prompts" / "responsive_bot.md").read_text()
    assert "Telegram is short — long content goes on the relay" in body
    assert "HARD GATE" in body
    # Cross-reference in Hard prohibitions list
    assert "NEVER inline text-heavy content" in body
