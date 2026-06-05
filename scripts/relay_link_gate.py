#!/usr/bin/env python3
"""Relay-link hard gate hook for the responsive channel-claude session.

User directive (2026-06-05, codified in system_prompts/responsive_bot.md):

  Telegram = notifications + short answers + links.
  files.<your-domain> (the relay) = the actual documents.

Any text-heavy artifact (transcript, plan, log, code dump, multi-section
report, audit doc, review memo) MUST be published via `soma-relay` and
delivered as a SHORT message + the link, NEVER inlined into the Telegram
reply. This hook is the deterministic floor that enforces the rule when
the bot drifts.

Wiring: registered in hooks/hooks.json under PreToolUse with matchers:

  mcp__hermes_api__send_tg_reply
  mcp__plugin_telegram_telegram__reply

Heuristic for "text-heavy" — DENY if ANY of these hits:

  1. Length: >1500 chars (the user's stated soft cap).
  2. Code-block weight: a fenced ``` block longer than 10 lines (excludes
     short shell snippets / one-liners which are fine inline).
  3. Multi-section markdown: 2 or more lines starting with `## ` or `### `
     (reads like a document, not a message).

Bypass marker — if the model genuinely needs to send a long reply (rare;
the operator may explicitly request inline output for one-shot debug
sessions), it can prefix the body with the literal token

  [RELAY-INLINE-OK]

on its own first line. The hook strips that marker before allowing the
send through. Use sparingly — it's an audit-trail-leaving escape hatch,
not a routine bypass.

Coverage gap (documented + accepted): if the bot replies via the raw
text channel without calling send_tg_reply, the hook cannot fire. That
path is covered by the prompt-level contract only. In practice the
markdown-capable replies route through send_tg_reply (per the
hermes_api/server.py docstring), so the hook catches the common case.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

SEND_REPLY_TOOLS = {
    "mcp__hermes_api__send_tg_reply",
    "mcp__plugin_telegram_telegram__reply",
}

LENGTH_LIMIT = int(os.environ.get("HERMES_RELAY_GATE_LENGTH_LIMIT", "1500"))
CODE_BLOCK_LINE_LIMIT = int(
    os.environ.get("HERMES_RELAY_GATE_CODE_BLOCK_LINE_LIMIT", "10")
)
HEADING_COUNT_LIMIT = int(
    os.environ.get("HERMES_RELAY_GATE_HEADING_COUNT_LIMIT", "2")
)
BYPASS_TOKEN = "[RELAY-INLINE-OK]"

# Patterns
_FENCE_RE = re.compile(r"```")
_HEADING_RE = re.compile(r"^#{2,3}\s+\S", re.MULTILINE)


def _log(event: dict, action: str, **extra) -> None:
    try:
        path = Path.home() / ".claude-soma" / "activity.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "source": "relay_link_gate",
                        "action": action,
                        "session_id": event.get("session_id", "unknown"),
                        **extra,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def _allow() -> None:
    json.dump({}, sys.stdout)
    sys.exit(0)


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def _longest_fenced_block(text: str) -> int:
    """Return the line count of the longest ``` ... ``` block, 0 if none."""
    fences = [m.start() for m in _FENCE_RE.finditer(text)]
    if len(fences) < 2:
        return 0
    longest = 0
    for i in range(0, len(fences) - 1, 2):
        start, end = fences[i], fences[i + 1]
        block = text[start:end]
        # Subtract the opening fence line itself + closing fence line
        lines = block.count("\n")
        if lines > longest:
            longest = lines
    return longest


def _classify(text: str) -> tuple[bool, str]:
    """Return (is_text_heavy, reason). reason is a short human string."""
    n = len(text)
    if n > LENGTH_LIMIT:
        return (True, f"body length {n} > {LENGTH_LIMIT} chars")
    code_lines = _longest_fenced_block(text)
    if code_lines > CODE_BLOCK_LINE_LIMIT:
        return (
            True,
            f"fenced code block has ~{code_lines} lines (> {CODE_BLOCK_LINE_LIMIT})",
        )
    heading_hits = len(_HEADING_RE.findall(text))
    if heading_hits >= HEADING_COUNT_LIMIT:
        return (
            True,
            f"{heading_hits} markdown section headings detected — reads like a document",
        )
    return (False, "")


def _build_deny_reason(text: str, classify_reason: str) -> str:
    return (
        "Relay-link HARD GATE: this Telegram reply is text-heavy "
        f"({classify_reason}). Per system prompt rule "
        '"Telegram is short — long content goes on the relay" '
        "(same weight as the Heard-echo gate), this body MUST be:\n"
        "\n"
        "  1. Written to a file, e.g.:\n"
        "       printf '%s' '<your full content>' > /tmp/<short-slug>.md\n"
        "  2. Published via the relay:\n"
        "       URL=$(soma-relay publish /tmp/<short-slug>.md)\n"
        "  3. Re-sent as a SHORT Telegram message + the URL:\n"
        "       <one-line headline> + <a blank line> + <$URL>\n"
        "\n"
        "Inline replies are for notifications, short answers, and links.\n"
        "Long artifacts (transcripts, plans, logs, code dumps, multi-section\n"
        "docs, reviews, audit reports) belong on the relay.\n"
        "\n"
        "If you genuinely need a one-shot inline bypass (rare — e.g. the\n"
        "operator explicitly asked you to paste the raw output this once),\n"
        f"prefix your reply with the literal token `{BYPASS_TOKEN}` on its\n"
        "own first line. It's stripped before the send and audit-logged."
    )


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = event.get("tool_name", "")
    hook_event = event.get("hook_event_name", "")
    if hook_event != "PreToolUse" or tool not in SEND_REPLY_TOOLS:
        sys.exit(0)
    text = event.get("tool_input", {}).get("text", "")
    if not isinstance(text, str):
        text = str(text)
    # Bypass marker honored on the first non-blank line.
    stripped = text.lstrip()
    if stripped.startswith(BYPASS_TOKEN):
        _log(event, "bypass_marker_used", body_len=len(text))
        _allow()
    heavy, reason = _classify(text)
    if not heavy:
        _allow()
    _log(
        event,
        "relay_gate_deny",
        classify_reason=reason,
        body_len=len(text),
    )
    _deny(_build_deny_reason(text, reason))


if __name__ == "__main__":
    main()
