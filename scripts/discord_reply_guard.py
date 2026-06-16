#!/usr/bin/env python3
"""Stop hook: guarantee that a Discord-triggered turn cannot end in operator-facing silence.

Wiring: registered in hooks/hooks.json under "Stop", alongside tg_reply_guard.py.
Fires once per turn end.

Mirrors tg_reply_guard.py for the Discord channel. The Discord bot token is managed
by the discord plugin and is not available from a stable secrets path, so this guard
has no auto-relay fallback: it only blocks once and reinjects an imperative to call the
Discord reply tool. On a second miss it logs and allows the stop (cannot loop forever).

Kill switch: SOMA_DISCORD_REPLY_GUARD_DISABLED=1 -> immediate exit 0.
Fail-open: any unhandled exception -> exit 0. Never bricks the channel.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

GUARD_FLAG_PREFIX = "claude-soma-discord-guard-"
FLAG_DIR = Path("/tmp")
FLAG_TTL_SECONDS = 600
TAIL_SIZE = 2 * 1024 * 1024  # 2 MB

SEND_TOOL_RE = re.compile(r"^mcp__plugin_discord_discord__reply$")
CHANNEL_TAG_RE = re.compile(r'<channel\s[^>]*source="plugin:discord:discord')
CHAT_ID_RE = re.compile(r'chat_id="([^"]+)"')
MESSAGE_ID_RE = re.compile(r'message_id="([^"]+)"')


def _safe_id(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))


def _guard_flag_path(session_id: str) -> Path:
    return FLAG_DIR / f"{GUARD_FLAG_PREFIX}{_safe_id(session_id)}"


def _sweep_stale_flags() -> None:
    now = time.time()
    try:
        for entry in FLAG_DIR.iterdir():
            if not entry.name.startswith(GUARD_FLAG_PREFIX):
                continue
            try:
                if now - entry.stat().st_mtime > FLAG_TTL_SECONDS:
                    entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _log_activity(session_id: str, action: str, **extra: object) -> None:
    try:
        path = Path.home() / ".claude-soma" / "activity.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "source": "discord_reply_guard",
                        "action": action,
                        "session_id": session_id,
                        **extra,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def _parse_transcript_tail(transcript_path: str) -> list[dict]:
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
        lines = lines[1:]

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


def _find_turn_window(
    entries: list[dict],
) -> tuple[str | None, str | None, list[dict]]:
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if not CHANNEL_TAG_RE.search(content):
            return None, None, []
        chat_id_m = CHAT_ID_RE.search(content)
        msg_id_m = MESSAGE_ID_RE.search(content)
        chat_id = chat_id_m.group(1) if chat_id_m else ""
        message_id = msg_id_m.group(1) if msg_id_m else ""
        window = entries[i + 1:]
        return chat_id, message_id, window

    return None, None, []


def _extract_tool_result_text(result_block: dict) -> str:
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
            if item.get("is_error") is True:
                continue
            if item.get("is_error") is None:
                text = _extract_tool_result_text(item).lower()
                if "error" in text or "denied" in text:
                    continue
            return True

    return False


def _collect_assistant_text(window: list[dict]) -> str:
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


def main() -> None:
    if os.environ.get("SOMA_DISCORD_REPLY_GUARD_DISABLED") == "1":
        sys.exit(0)

    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session_id = str(event.get("session_id") or "unknown")
    transcript_path = str(event.get("transcript_path") or "")
    cwd = str(event.get("cwd") or "")
    stop_hook_active = bool(event.get("stop_hook_active", False))

    mode = os.environ.get("SOMA_DISCORD_REPLY_GUARD_MODE", "block")

    if cwd != "/opt/claude-soma":
        sys.exit(0)

    _sweep_stale_flags()

    if not transcript_path:
        sys.exit(0)

    entries = _parse_transcript_tail(transcript_path)

    chat_id, message_id, window = _find_turn_window(entries)
    if chat_id is None:
        sys.exit(0)

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

    assistant_text = _collect_assistant_text(window)

    if mode == "log":
        _log_activity(
            session_id,
            "would_block",
            chat_id=chat_id,
            message_id=message_id,
            assistant_text_present=bool(assistant_text),
            stop_hook_active=stop_hook_active,
        )
        sys.exit(0)

    if not stop_hook_active:
        reason = (
            f"HARD GATE: this turn was triggered by a Discord message "
            f"(chat_id={chat_id}, message_id={message_id}) but no successful "
            f"mcp__plugin_discord_discord__reply was made. Your text reply never "
            f"reached the operator. Call mcp__plugin_discord_discord__reply NOW with "
            f"your reply (pass chat_id={chat_id} back; omit reply_to for a normal "
            f"response), then end the turn."
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

    _log_activity(
        session_id,
        "block_attempt2_allow",
        chat_id=chat_id,
        message_id=message_id,
        assistant_text_present=bool(assistant_text),
    )
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
