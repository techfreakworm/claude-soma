#!/usr/bin/env python3
"""Heard-echo hard gate hook for the responsive channel-claude session.

Wiring: registered TWICE in hooks/hooks.json —

  * PostToolUse matcher `mcp__voice-stt__transcribe` → stamps a per-session
    flag file when the model transcribes a voice note. The flag carries
    the raw transcript so the deny path can quote it back.

  * PreToolUse matcher `mcp__hermes_api__send_tg_reply` (canonical
    markdown-reply path) and `mcp__plugin_telegram_telegram__reply`
    (plain-text-ack path) → if the flag is set and the outgoing `text`
    field does NOT begin with `Heard: "..."`, deny the send with a clear
    actionable reason so the model rewrites the reply.

Coverage gap: if the channel auto-sends the model's raw text response
without going through send_tg_reply / telegram__reply, the hook never
fires. That path is still covered by the system-prompt hard rule in
system_prompts/responsive_bot.md ("Voice notes: ALWAYS echo the
transcript — HARD GATE"). The hook is best-effort deterministic
enforcement for the common send-path; the prompt is the contract.

Flag layout:

  /tmp/claude-soma-heard-pending-<session_id>

Contents: one line, the raw transcript text. Created by the PostToolUse
voice-stt branch, deleted by the PreToolUse send-reply branch on either
acceptance OR denial — denial means the model retries WITH the echo;
the next send-reply call should be allowed. (If the model never sends
a reply at all in this turn, the flag is stale and cleaned up by the
TTL sweep below.)

TTL: flags older than 600 s (10 min) are removed unconditionally during
every hook invocation, so a model that never replies doesn't leak files.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FLAG_DIR = Path("/tmp")
FLAG_PREFIX = "claude-soma-heard-pending-"
FLAG_TTL_SECONDS = 600

SEND_REPLY_TOOLS = {
    "mcp__hermes_api__send_tg_reply",
    "mcp__plugin_telegram_telegram__reply",
}
VOICE_STT_TOOLS = {
    "mcp__voice-stt__transcribe",
}


def _session_id(event: dict) -> str:
    return (
        event.get("session_id")
        or event.get("hookSpecificOutput", {}).get("session_id")
        or "unknown"
    )


def _flag_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return FLAG_DIR / f"{FLAG_PREFIX}{safe}"


def _sweep_stale_flags() -> None:
    now = time.time()
    try:
        for entry in FLAG_DIR.iterdir():
            if not entry.name.startswith(FLAG_PREFIX):
                continue
            try:
                if now - entry.stat().st_mtime > FLAG_TTL_SECONDS:
                    entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _log(event: dict, action: str, **extra) -> None:
    try:
        path = Path.home() / ".claude-soma" / "activity.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "source": "heard_gate",
                        "action": action,
                        "session_id": _session_id(event),
                        **extra,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def _allow_pretooluse() -> None:
    # Empty JSON object = allow; explicit form is also valid.
    json.dump({}, sys.stdout)
    sys.exit(0)


def _deny_pretooluse(reason: str) -> None:
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


def _handle_post_voice_stt(event: dict) -> None:
    """Voice-STT just ran. Stamp the Heard-pending flag with the transcript."""
    sid = _session_id(event)
    flag = _flag_path(sid)
    transcript = ""
    response = event.get("tool_response", event.get("response", {}))
    if isinstance(response, dict):
        transcript = (
            response.get("text")
            or response.get("transcript")
            or response.get("result", {}).get("text", "")
            if isinstance(response.get("result"), dict)
            else response.get("transcript", "")
        )
        if not transcript and isinstance(response.get("content"), list):
            for chunk in response["content"]:
                if isinstance(chunk, dict) and "text" in chunk:
                    transcript = chunk["text"]
                    break
    elif isinstance(response, str):
        transcript = response
    try:
        flag.write_text(transcript[:4000], encoding="utf-8")
        _log(event, "voice_stt_flag_set", flag=str(flag), transcript_len=len(transcript))
    except OSError as exc:
        _log(event, "voice_stt_flag_set_failed", error=str(exc))
    sys.exit(0)


def _handle_pre_send_reply(event: dict) -> None:
    """Reply send is about to fire. If Heard flag set, validate the body."""
    sid = _session_id(event)
    flag = _flag_path(sid)
    if not flag.exists():
        _allow_pretooluse()
    text = event.get("tool_input", {}).get("text", "")
    if not isinstance(text, str):
        text = str(text)
    stripped = text.lstrip()
    if stripped.startswith("Heard:") or stripped.startswith('Heard "') or stripped.startswith("Heard \""):
        # Echo present — consume the flag and allow.
        try:
            flag.unlink()
        except OSError:
            pass
        _log(event, "heard_echo_present_allow")
        _allow_pretooluse()
    # Echo missing — load the transcript to include in the deny reason
    # so the model knows exactly what to put in the echo.
    try:
        transcript = flag.read_text(encoding="utf-8").strip()
    except OSError:
        transcript = ""
    # Drop the flag so the model's retry (which SHOULD have the echo) doesn't
    # double-deny if the retry is fast and a stale flag survives.
    try:
        flag.unlink()
    except OSError:
        pass
    _log(event, "heard_echo_missing_deny", transcript_preview=transcript[:200])
    transcript_for_quote = transcript.replace('"', '\\"')[:1500] or "<the transcript voice-stt returned>"
    reason = (
        "Heard-echo HARD GATE: this turn called voice-stt, but the outgoing "
        "reply does not begin with `Heard:`. Per system prompt + voice-action "
        "skill, every voice-note reply MUST start with:\n\n"
        f'    Heard: "{transcript_for_quote}"\n\n'
        "Rewrite the reply with the Heard: line on its own line as the first "
        "content, followed by a blank line, then the rest of your response, "
        "then retry this send."
    )
    _deny_pretooluse(reason)


def main() -> None:
    _sweep_stale_flags()
    try:
        event = json.load(sys.stdin)
    except Exception:
        # Hook contract: on parse error, exit 0 (no-op) so we never block.
        sys.exit(0)
    tool = event.get("tool_name", "")
    hook_event = event.get("hook_event_name", "")
    if hook_event == "PostToolUse" and tool in VOICE_STT_TOOLS:
        _handle_post_voice_stt(event)
        return
    if hook_event == "PreToolUse" and tool in SEND_REPLY_TOOLS:
        _handle_pre_send_reply(event)
        return
    # Any other tool/event combination is none of our business.
    sys.exit(0)


if __name__ == "__main__":
    main()
