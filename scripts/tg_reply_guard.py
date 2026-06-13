#!/usr/bin/env python3
"""Stop hook: guarantee that a Telegram-triggered turn cannot end in operator-facing silence.

Wiring: registered in hooks/hooks.json under "Stop". Fires once per turn end.

How it works
============
On every Stop event the hook reads the tail of the session transcript (last 2 MB) and
answers three questions:

  1. Was this turn Telegram-triggered? Scan backward for the most recent user entry whose
     message.content is a plain string (not a list of tool_results) and contains a
     <channel source="plugin:telegram...> tag. If no such entry exists, or it lacks the
     channel prefix, exit 0 (task-notifications, cron, local TUI, hook-injected turns are
     all exempt). Extract chat_id and message_id from the tag.

  2. Was a reply delivered? Within the turn window (all entries after that user entry),
     look for a tool_use of mcp__hermes_api__send_tg_reply or
     mcp__plugin_telegram_telegram__reply, AND a matching tool_result (by tool_use_id)
     that is not is_error:true and whose text contains no error marker. A hook-denied
     send yields an error result — correctly excluded.

  3. Did the model produce user-facing text? Concatenate assistant text blocks (not
     thinking blocks) from the turn window.

Decision (Telegram-triggered turns only):
  delivered            -> record message_id + allow stop.
  not delivered, mode=log    -> write activity.jsonl telemetry, allow stop.
  not delivered, attempt 1   -> emit {"decision":"block","reason":"..."} to stdout.
  not delivered, attempt 2, mode=block   -> log + allow stop (cannot loop forever).
  not delivered, attempt 2, mode=enforce -> AUTO-RELAY: send assistant text via Bot API
                                            curl, then allow stop.

Modes (SOMA_TG_REPLY_GUARD_MODE env var):
  log     (DEFAULT) -- detect only; write telemetry; NEVER block or send. Safe to deploy
                       first to observe without any operator impact.
  block             -- block/reinject on attempt 1; log+allow on attempt 2; no relay.
  enforce           -- block/reinject on attempt 1; auto-relay via Bot API on attempt 2.

Kill switch: SOMA_TG_REPLY_GUARD_DISABLED=1 -> immediate exit 0.
Fail-open: any unhandled exception -> exit 0. Never bricks the channel.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GUARD_FLAG_PREFIX = "claude-soma-tg-guard-"
HEARD_FLAG_PREFIX = "claude-soma-heard-pending-"
FLAG_DIR = Path("/tmp")
FLAG_TTL_SECONDS = 600
TAIL_SIZE = 2 * 1024 * 1024  # 2 MB

SEND_TOOL_RE = re.compile(
    r"^mcp__(hermes[-_]api__send_tg_reply|plugin_telegram_telegram__reply)$"
)
CHANNEL_TAG_RE = re.compile(r'<channel\s[^>]*source="plugin:telegram')
CHAT_ID_RE = re.compile(r'chat_id="([^"]+)"')
MESSAGE_ID_RE = re.compile(r'message_id="([^"]+)"')

TRUNCATE_MAX = 3900
TRUNCATE_SUFFIX = " ... [truncated; full text in session transcript]"
AUTO_RELAY_PREFIX = "[auto-relay] "
CURL_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Flag helpers
# ---------------------------------------------------------------------------

def _safe_id(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))


def _guard_flag_path(session_id: str) -> Path:
    return FLAG_DIR / f"{GUARD_FLAG_PREFIX}{_safe_id(session_id)}"


def _heard_flag_path(session_id: str) -> Path:
    return FLAG_DIR / f"{HEARD_FLAG_PREFIX}{_safe_id(session_id)}"


def _sweep_stale_flags() -> None:
    now = time.time()
    try:
        for entry in FLAG_DIR.iterdir():
            if not (
                entry.name.startswith(GUARD_FLAG_PREFIX)
                or entry.name.startswith(HEARD_FLAG_PREFIX)
            ):
                continue
            try:
                if now - entry.stat().st_mtime > FLAG_TTL_SECONDS:
                    entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Secrets + logging helpers
# ---------------------------------------------------------------------------

def _read_secrets_var(name: str, secrets: str = "/etc/claude-soma/secrets.env") -> str:
    """Return the LAST occurrence of `name=value` from secrets.env, stripping outer quotes.

    Mirrors the _read_secrets_var helper in scripts/engagement-hourly-drip.py.
    Returns empty string if the file is unreadable or the key is absent.
    """
    try:
        with open(secrets, encoding="utf-8") as fh:
            last = ""
            for line in fh:
                if not line.startswith(name + "="):
                    continue
                v = line[len(name) + 1:].rstrip("\n").rstrip("\r")
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                last = v
            return last
    except OSError:
        return ""


def _log_activity(session_id: str, action: str, **extra: object) -> None:
    try:
        path = Path.home() / ".claude-soma" / "activity.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "source": "tg_reply_guard",
                        "action": action,
                        "session_id": session_id,
                        **extra,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def _parse_transcript_tail(transcript_path: str) -> list[dict]:
    """Read the last TAIL_SIZE bytes of the jsonl, return a list of parsed entries.

    Drops the first (possibly partial) line after seeking. Skips any line that
    fails json.loads. Returns [] on any file error.
    """
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - TAIL_SIZE)
            fh.seek(start)
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    lines = raw.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # drop partial first line

    entries: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except (json.JSONDecodeError, ValueError):
            pass
    return entries


# ---------------------------------------------------------------------------
# Turn-window extraction
# ---------------------------------------------------------------------------

def _find_turn_window(
    entries: list[dict],
) -> tuple[str | None, str | None, list[dict]]:
    """Scan backward for the most recent string-content user entry with the
    Telegram channel tag.

    Returns (chat_id, message_id, window_entries) where window_entries is every
    entry AFTER the triggering user entry. Returns (None, None, []) if not found
    or not Telegram-triggered.
    """
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", "")
        # A real inbound message has string content; tool_results have list content.
        if not isinstance(content, str):
            continue
        # Found the most recent user-prompt entry.
        if not CHANNEL_TAG_RE.search(content):
            # Not Telegram-triggered (local TUI, task-notification, etc.)
            return None, None, []
        chat_id_m = CHAT_ID_RE.search(content)
        msg_id_m = MESSAGE_ID_RE.search(content)
        chat_id = chat_id_m.group(1) if chat_id_m else ""
        message_id = msg_id_m.group(1) if msg_id_m else ""
        window = entries[i + 1:]
        return chat_id, message_id, window

    return None, None, []


# ---------------------------------------------------------------------------
# Delivery detection
# ---------------------------------------------------------------------------

def _extract_tool_result_text(result_block: dict) -> str:
    """Flatten the content of a tool_result block to a single string."""
    c = result_block.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for item in c:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(c) if c else ""


def _check_reply_delivered(window: list[dict]) -> bool:
    """Return True if the window contains a successful send_tg_reply tool call.

    Successful means: matching tool_use name + corresponding tool_result with
    is_error != true and no obvious error marker in the result text.
    """
    # Collect tool_use ids that match the send-reply regex
    send_ids: set[str] = set()
    for entry in window:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and SEND_TOOL_RE.match(
                item.get("name", "")
            ):
                send_ids.add(item["id"])

    if not send_ids:
        return False

    # Check for a successful tool_result for any of those ids
    for entry in window:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_result":
                continue
            if item.get("tool_use_id") not in send_ids:
                continue
            # Found a result for one of our send calls. `is_error` is the
            # authoritative MCP signal; trust it when present. The substring
            # heuristic is only a fallback for results that omit the flag —
            # gating it this way avoids misclassifying a successful payload
            # whose body merely mentions "error"/"denied" (e.g. "0 errors").
            if item.get("is_error") is True:
                continue  # hook-denied or error; not delivered
            if item.get("is_error") is None:
                text = _extract_tool_result_text(item).lower()
                if "error" in text or "denied" in text:
                    continue  # no authoritative flag + error-shaped text; treat as not delivered
            return True

    return False


# ---------------------------------------------------------------------------
# Assistant text collection
# ---------------------------------------------------------------------------

def _collect_assistant_text(window: list[dict]) -> str:
    """Concatenate all assistant text blocks from the window (exclude thinking blocks)."""
    parts: list[str] = []
    for entry in window:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                t = item.get("text", "")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Auto-relay (attempt 2 enforcement)
# ---------------------------------------------------------------------------

def _do_auto_relay(
    session_id: str,
    chat_id: str,
    assistant_text: str,
) -> None:
    """Send the assistant text via Telegram Bot API curl and log the action."""
    # Build body
    heard_flag = _heard_flag_path(session_id)
    heard_prefix = ""
    try:
        if heard_flag.exists():
            transcript_line = heard_flag.read_text(encoding="utf-8").strip()
            heard_prefix = f'Heard: "{transcript_line}"\n'
            heard_flag.unlink()
    except OSError:
        pass

    if assistant_text:
        body = heard_prefix + assistant_text if heard_prefix else assistant_text
    else:
        body = (
            "[auto-relay] Turn completed with no reply composed; "
            "check /api session status."
        )
        heard_prefix = ""  # fixed message already covers it

    body = AUTO_RELAY_PREFIX + body

    if len(body) > TRUNCATE_MAX:
        body = body[: TRUNCATE_MAX - len(TRUNCATE_SUFFIX)] + TRUNCATE_SUFFIX

    # Resolve token and chat fallback
    token = _read_secrets_var("TELEGRAM_BOT_TOKEN")
    if not token:
        _log_activity(
            session_id,
            "auto_relay_failed",
            reason="no_token",
            chat_id=chat_id,
        )
        return

    effective_chat_id = chat_id or _read_secrets_var("HERMES_NOTIFY_CHAT_ID")
    if not effective_chat_id:
        _log_activity(
            session_id,
            "auto_relay_failed",
            reason="no_chat_id",
        )
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": effective_chat_id, "text": body}

    try:
        subprocess.run(
            [
                "curl",
                "-s",
                "-X",
                "POST",
                url,
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=CURL_TIMEOUT,
            check=True,
        )
        _log_activity(
            session_id,
            "auto_relay",
            chat_id=effective_chat_id,
            body_len=len(body),
            heard_prefix=bool(heard_prefix),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _log_activity(
            session_id,
            "auto_relay_failed",
            reason=str(exc)[:200],
            chat_id=effective_chat_id,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if os.environ.get("SOMA_TG_REPLY_GUARD_DISABLED") == "1":
        sys.exit(0)

    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session_id = str(event.get("session_id") or "unknown")
    transcript_path = str(event.get("transcript_path") or "")
    cwd = str(event.get("cwd") or "")
    stop_hook_active = bool(event.get("stop_hook_active", False))

    mode = os.environ.get("SOMA_TG_REPLY_GUARD_MODE", "log")

    # Non-bot guard: only operate on the live bot session
    if cwd != "/opt/claude-soma":
        sys.exit(0)

    # Sweep stale /tmp flags only for the bot session (after the cwd guard,
    # so non-bot sessions never do a pointless /tmp scan on every Stop).
    _sweep_stale_flags()

    if not transcript_path:
        sys.exit(0)

    entries = _parse_transcript_tail(transcript_path)

    chat_id, message_id, window = _find_turn_window(entries)
    if chat_id is None:
        sys.exit(0)

    # Dedup / --continue guard: skip if this message_id was already satisfied
    guard_flag = _guard_flag_path(session_id)
    try:
        recorded = guard_flag.read_text(encoding="utf-8").strip() if guard_flag.exists() else ""
    except OSError:
        recorded = ""
    if recorded and recorded == str(message_id):
        sys.exit(0)

    delivered = _check_reply_delivered(window)

    if delivered:
        try:
            guard_flag.write_text(str(message_id), encoding="utf-8")
        except OSError:
            pass
        _log_activity(session_id, "delivered", chat_id=chat_id, message_id=message_id)
        sys.exit(0)

    # Not delivered — enforce per mode
    assistant_text = _collect_assistant_text(window)

    if mode == "log":
        action = "would_relay" if stop_hook_active else "would_block"
        _log_activity(
            session_id,
            action,
            chat_id=chat_id,
            message_id=message_id,
            assistant_text_present=bool(assistant_text),
            stop_hook_active=stop_hook_active,
        )
        sys.exit(0)

    if not stop_hook_active:
        # Attempt 1: BLOCK / REINJECT (both block and enforce modes)
        reason = (
            f"HARD GATE: this turn was triggered by a Telegram message "
            f"(chat_id={chat_id}, message_id={message_id}) but no successful "
            f"send_tg_reply / telegram__reply was made. Your text reply never "
            f"reached the operator. Call mcp__hermes_api__send_tg_reply NOW with "
            f"your reply (or a one-line ack if work was dispatched), then end the "
            f"turn. Remember the Heard: echo rule if this was a voice note."
        )
        _log_activity(
            session_id,
            "block_attempt1",
            chat_id=chat_id,
            message_id=message_id,
            assistant_text_present=bool(assistant_text),
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        sys.exit(0)

    # stop_hook_active == True: attempt 2
    if mode == "block":
        _log_activity(
            session_id,
            "block_attempt2_allow",
            chat_id=chat_id,
            message_id=message_id,
            assistant_text_present=bool(assistant_text),
        )
        sys.exit(0)

    # mode == "enforce": AUTO-RELAY
    _do_auto_relay(session_id, chat_id, assistant_text)
    try:
        guard_flag.write_text(str(message_id), encoding="utf-8")
    except OSError:
        pass
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
