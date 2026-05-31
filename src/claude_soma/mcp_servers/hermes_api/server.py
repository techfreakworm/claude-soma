from __future__ import annotations

import datetime
import html
import http.server
import json
import mimetypes
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import claude_state
from .notify_store import EventStore, VALID_TYPES, URGENT_TYPES
from .socket import serve_blocking
from .tg_html import chunk_html_for_telegram, gfm_to_html


# ---- MCP tools exposed to the channel session ------------------------------

mcp = FastMCP("hermes_api")


@mcp.tool()
def list_active_sessions() -> list[dict]:
    """List background Claude sessions managed by the agent view supervisor."""
    return claude_state.list_sessions()


@mcp.tool()
def read_activity_log(limit: int = 200) -> list[dict]:
    """Read recent PostToolUse activity log lines."""
    return claude_state.read_activity_log(limit)


@mcp.tool()
def read_memory(project_slug: str) -> str:
    """Read MEMORY.md for a given Claude project slug."""
    return claude_state.read_memory(project_slug)


@mcp.tool()
def list_transcript_threads(limit: int = 50) -> list[dict]:
    """List recent transcript threads across all projects."""
    return claude_state.list_transcript_threads(limit)


@mcp.tool()
def read_transcript(thread_id: str, project: str) -> list[dict]:
    """Read a transcript as list of message events."""
    return claude_state.read_transcript(thread_id, project)


@mcp.tool()
def get_recent_lead_events(lead: str | None = None, limit: int = 20) -> list[dict]:
    """Query recent lead lifecycle events.

    If lead is None, returns events across all leads ordered newest-first.
    Use this to check what a lead has reported recently without needing to
    capture-pane its tmux session.
    """
    return _store.get_recent(lead=lead, limit=limit)


@mcp.tool()
def resolve_pending_input(event_id: int, answer: str) -> dict:
    """Mark a NEEDS_INPUT event as resolved with the given answer.

    Call this after the user's reply has been routed to the relevant lead.
    Updates pending_inputs.status = 'resolved' so the UserPromptSubmit hook
    stops injecting the open question into future turns.

    Returns: {"resolved": true} or {"resolved": false} if not found/already resolved.
    """
    resolved = _store.mark_pending_resolved(event_id, answer)
    return {"resolved": resolved}


# ---- One-shot Telegram reminder ------------------------------------------

def _parse_when(when: str) -> float:
    """Return the delay in seconds until the reminder should fire.

    Accepts:
    - Relative: "5m", "2h", "1d" (minutes / hours / days)
    - Unix epoch: a numeric string like "1750000000" (seconds since epoch)
    - ISO 8601: "2026-06-01T09:00:00", "2026-06-01T09:00:00Z", etc.
    """
    s = when.strip()

    # Relative (5m / 2h / 1d — case-insensitive)
    m = re.fullmatch(r"(\d+)(m|h|d)", s, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        return val * {"m": 60, "h": 3600, "d": 86400}[unit]

    # Unix epoch (all-digit, 9+ chars so "60" isn't mistaken for epoch)
    if re.fullmatch(r"\d{9,}", s):
        epoch = float(s)
        delay = epoch - time.time()
        if delay <= 0:
            raise ValueError(f"Unix epoch {s!r} is in the past")
        return delay

    # ISO 8601 via datetime.fromisoformat (Python 3.7+)
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"Cannot parse 'when' value: {when!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    delay = dt.timestamp() - time.time()
    if delay <= 0:
        raise ValueError(f"ISO timestamp {s!r} is in the past")
    return delay


@mcp.tool()
def schedule_reminder(when: str, message: str) -> dict:
    """Schedule a one-shot Telegram reminder.

    Spawns a detached bash subprocess that sleeps until the target time, then
    sends a Telegram message via the Bot API. The process survives parent exit
    (start_new_session=True). CHAT_ID is read from HERMES_NOTIFY_CHAT_ID.

    Args:
        when: When to fire. Accepts relative ("5m", "2h", "1d"),
              ISO 8601 ("2026-06-01T09:00:00Z"), or Unix epoch ("1750000000").
        message: Text to send (plain text, no HTML).

    Returns:
        {"pid": int, "fires_at_iso": str, "message_preview": str}
    """
    delay = _parse_when(when)
    delay_secs = max(1, int(delay))

    fires_at = datetime.datetime.fromtimestamp(
        time.time() + delay_secs, tz=datetime.timezone.utc
    )
    fires_at_iso = fires_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    reminder_id = uuid.uuid4().hex[:8]
    log_path = f"/tmp/reminder-{reminder_id}.log"

    chat_id = _notify_chat_id()

    bash_script = (
        f"sleep {delay_secs}\n"
        f"source ~/.claude/channels/telegram/.env\n"
        f"curl -sX POST \"https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage\""
        f" --data-urlencode \"chat_id={chat_id}\""
        f" --data-urlencode \"text=$REMINDER_TEXT\"\n"
    )

    env = os.environ.copy()
    env["REMINDER_TEXT"] = message

    log_file = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            ["bash", "-c", bash_script],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
    finally:
        log_file.close()

    return {
        "pid": proc.pid,
        "fires_at_iso": fires_at_iso,
        "message_preview": message[:100],
    }


# ---- Routines cache prewarm -----------------------------------------------

def _prewarm_routines_cache() -> None:
    """Populate the cloud-routines cache so the first dashboard load is fast.

    Imports the live cache function from the API routes layer and calls it.
    No-ops silently if the import fails (e.g. the API package is not installed
    in this environment).
    """
    try:
        from claude_soma.api.routes.routines import _query_cloud_routines_cached
        _query_cloud_routines_cached()
    except Exception:
        pass


# ---- Telegram Bot API helpers ---------------------------------------------

_TG_ENV_FILE = Path.home() / ".claude" / "channels" / "telegram" / ".env"
_TG_API_BASE = "https://api.telegram.org"


def _load_tg_token() -> str:
    """Load TELEGRAM_BOT_TOKEN from env, then from ~/.claude/channels/telegram/.env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    try:
        for line in _TG_ENV_FILE.read_text().splitlines():
            m_line = line.strip()
            if m_line and not m_line.startswith('#') and '=' in m_line:
                key, _, val = m_line.partition('=')
                if key.strip() == 'TELEGRAM_BOT_TOKEN':
                    return val.strip()
    except OSError:
        pass
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN not set in env and not found in "
        f"{_TG_ENV_FILE}"
    )


def _tg_post_json(url: str, payload: dict) -> dict:
    """POST JSON to a Telegram Bot API endpoint. Raise RuntimeError on failure."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        snippet = raw[-500:].decode(errors='replace')
        raise RuntimeError(f"Telegram API error {exc.code}: {snippet}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram request failed: {exc.reason}") from exc


def _build_multipart(
    fields: dict[str, str],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    """Build a multipart/form-data body. Return (body_bytes, content_type_header)."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            .encode()
        )
    for field_name, filename, data, mime_type in files:
        header = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\n'
            f'Content-Type: {mime_type}\r\n\r\n'
        ).encode()
        parts.append(header + data + b'\r\n')
    body = b''.join(parts) + f'--{boundary}--\r\n'.encode()
    return body, f'multipart/form-data; boundary={boundary}'


def _tg_post_multipart(url: str, fields: dict[str, str], file_path: str) -> dict:
    """POST a file to a Telegram Bot API endpoint via multipart/form-data."""
    path = Path(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or 'application/octet-stream'
    data = path.read_bytes()

    # Determine field name by mime type
    field_name = 'photo' if (mime_type or '').startswith('image/') else 'document'
    body, content_type = _build_multipart(
        fields,
        [(field_name, path.name, data, mime_type)],
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        snippet = raw[-500:].decode(errors='replace')
        raise RuntimeError(f"Telegram file upload error {exc.code}: {snippet}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram file upload failed: {exc.reason}") from exc


_PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
# Telegram's bot-upload cap for sendDocument / sendPhoto is 50 MB. Files
# larger than this fall back to a placeholder line in the message text.
_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  # 50 MB


@mcp.tool()
def send_tg_reply(
    chat_id: str,
    text: str,
    files: list[str] | None = None,
    reply_to: str | None = None,
) -> dict:
    """Send a Telegram reply with GFM markdown rendered as HTML.

    Convert `text` from GitHub-flavored markdown to Telegram HTML, chunk
    at 4096 chars, and POST each chunk to the Telegram Bot API with
    parse_mode=HTML.

    Use this for ANY reply that contains markdown formatting (bold, italic,
    code, fenced code blocks, links, tables, headers, lists). Use the
    plugin's `mcp__plugin_telegram_telegram__reply` only for plain-text acks.

    Args:
        chat_id: Telegram chat ID (string).
        text: Reply text, may contain GitHub-flavored markdown.
        files: Optional list of absolute file paths to attach.
        reply_to: Optional message_id (as string) to reply to.
    """
    token = _load_tg_token()
    base = f"{_TG_API_BASE}/bot{token}"

    html_text = gfm_to_html(text)
    chunks = chunk_html_for_telegram(html_text)

    sent_ids: list[int] = []
    for i, chunk in enumerate(chunks):
        payload: dict = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
        }
        if reply_to is not None and i == 0:
            payload["reply_parameters"] = {"message_id": int(reply_to)}
        result = _tg_post_json(f"{base}/sendMessage", payload)
        sent_ids.append(result["result"]["message_id"])

    files_sent = 0
    for file_path in (files or []):
        path = Path(file_path)
        ext = path.suffix.lower()
        fields: dict[str, str] = {"chat_id": chat_id}
        if reply_to is not None:
            fields["reply_parameters"] = json.dumps({"message_id": int(reply_to)})
        if ext in _PHOTO_EXTS:
            result = _tg_post_multipart(f"{base}/sendPhoto", fields, file_path)
        else:
            result = _tg_post_multipart(f"{base}/sendDocument", fields, file_path)
        sent_ids.append(result["result"]["message_id"])
        files_sent += 1

    return {
        "sent_message_ids": sent_ids,
        "chunks": len(chunks),
        "files_sent": files_sent,
    }


# ---- Notify HTTP listener internals --------------------------------------

_NOTIFY_PORT_DEFAULT = 9100
_NOTIFY_CHAT_ID_DEFAULT = "935376085"
_MILESTONE_THROTTLE_SECS = int(
    os.environ.get("HERMES_NOTIFY_MILESTONE_THROTTLE_SECS", "300")
)
_MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB hard cap

# Module-level singletons (initialised in main() before threads start)
_store: EventStore = None  # type: ignore[assignment]
_milestone_last_dmed: dict[str, float] = {}  # {lead: ts}
_milestone_lock = threading.Lock()


def _notify_chat_id() -> str:
    return os.environ.get(
        "HERMES_NOTIFY_CHAT_ID",
        os.environ.get("TELEGRAM_CHAT_ID", _NOTIFY_CHAT_ID_DEFAULT),
    )


def _classify_attachments(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split a list of file paths into (sendable, oversized).

    sendable: paths that exist, are regular files, and are <= the 50 MB cap.
    oversized: existing regular files > 50 MB — these get rendered as
               placeholder lines in the message text instead of attached.
    Missing or non-regular paths are silently dropped (belt-and-suspenders;
    _format_completed_dm also filters before calling this).
    """
    sendable: list[str] = []
    oversized: list[str] = []
    for fp in paths:
        try:
            p = Path(fp)
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        if size <= _MAX_ATTACHMENT_BYTES:
            sendable.append(fp)
        else:
            oversized.append(fp)
    return sendable, oversized


def _send_proactive_dm(text: str, files: list[str] | None = None) -> int | None:
    """DM the user. Returns Telegram message_id or None on failure."""
    try:
        token = _load_tg_token()
        base = f"{_TG_API_BASE}/bot{token}"
        chat_id = _notify_chat_id()
        # The _format_*_dm helpers already produce Telegram HTML (with user
        # fields html-escaped); piping that through gfm_to_html would entity-
        # escape the <b>/<code>/<a>/<pre> tags themselves and Telegram would
        # render them as literal text. Chunk the pre-rendered HTML directly.
        chunks = chunk_html_for_telegram(text)
        last_msg_id: int | None = None
        for chunk in chunks:
            result = _tg_post_json(f"{base}/sendMessage", {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            })
            last_msg_id = result["result"]["message_id"]
        if files:
            fields = {"chat_id": chat_id}
            for fp in files:
                try:
                    path = Path(fp)
                    ext = path.suffix.lower()
                    if ext in _PHOTO_EXTS:
                        r = _tg_post_multipart(f"{base}/sendPhoto", fields, fp)
                    else:
                        r = _tg_post_multipart(f"{base}/sendDocument", fields, fp)
                    last_msg_id = r["result"]["message_id"]
                except Exception as att_exc:
                    _log_notify_error(f"attachment failed for {fp}: {att_exc}")
                    # Continue with the next file — text already delivered;
                    # partial attachment delivery is better than zero.
        return last_msg_id
    except Exception as exc:
        _log_notify_error(f"proactive DM failed: {exc}")
        return None


def _log_notify_error(msg: str) -> None:
    try:
        log_dir = Path("/var/log/claude-soma")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (log_dir / "hermes-notify.log").open("a").write(f"{ts} {msg}\n")
    except OSError:
        pass


def _format_started_dm(lead: str, payload: dict[str, Any]) -> str:
    desc = html.escape(str(payload.get("description", "")), quote=False)
    eta = html.escape(str(payload.get("eta", "")), quote=False)
    lead_e = html.escape(lead, quote=False)
    text = f"<b>Lead <code>{lead_e}</code> started:</b> {desc}"
    if eta:
        text += f"\nETA: {eta}"
    return text


def _format_milestone_dm(lead: str, milestones: list[dict[str, Any]]) -> str:
    lead_e = html.escape(lead, quote=False)
    if len(milestones) == 1:
        p = json.loads(milestones[0]["payload_json"])
        prog = html.escape(str(p.get("progress", "")), quote=False)
        pct = p.get("percent")
        eta_rem = html.escape(str(p.get("eta_remaining", "")), quote=False)
        text = f"<b>Lead <code>{lead_e}</code> milestone:</b> {prog}"
        if pct is not None:
            text += f" ({pct}%)"
        if eta_rem:
            text += f"\nETA remaining: {eta_rem}"
    else:
        lines = [f"<b>Lead <code>{lead_e}</code> milestones:</b>"]
        for ms in milestones:
            p = json.loads(ms["payload_json"])
            prog = html.escape(str(p.get("progress", "")), quote=False)
            pct = p.get("percent")
            line = f"• {prog}"
            if pct is not None:
                line += f" ({pct}%)"
            lines.append(line)
        text = "\n".join(lines)
    return text


def _format_completed_dm(lead: str, payload: dict[str, Any]) -> tuple[str, list[str]]:
    summary = html.escape(str(payload.get("summary", "")), quote=False)
    urls = payload.get("urls", [])
    paths = payload.get("paths", [])
    lead_e = html.escape(lead, quote=False)
    text = f"<b>Lead <code>{lead_e}</code> completed:</b>\n{summary}"

    sendable, oversized = _classify_attachments(list(paths))

    if urls:
        # URL goes into both href (attribute → quote=True) and link body (text)
        links = "\n".join(
            f'<a href="{html.escape(str(u), quote=True)}">'
            f'{html.escape(str(u), quote=False)}</a>'
            for u in urls
        )
        text += f"\n\n{links}"

    if oversized:
        placeholder_lines = []
        for fp in oversized:
            try:
                mb = Path(fp).stat().st_size / (1024 * 1024)
                placeholder_lines.append(
                    f"<i>[file too large for DM ({mb:.1f} MB), "
                    f"see {html.escape(fp, quote=False)}]</i>"
                )
            except OSError:
                placeholder_lines.append(
                    f"<i>[file too large for DM, "
                    f"see {html.escape(fp, quote=False)}]</i>"
                )
        text += "\n\n" + "\n".join(placeholder_lines)

    return text, sendable


def _format_needs_input_dm(lead: str, payload: dict[str, Any]) -> str:
    question = html.escape(str(payload.get("question", "")), quote=False)
    options = payload.get("options", [])
    lead_e = html.escape(lead, quote=False)
    text = (
        f"<b>Lead <code>{lead_e}</code> needs your input:</b>\n"
        f"{question}"
    )
    if options:
        opts = "\n".join(
            f"  • {html.escape(str(o), quote=False)}" for o in options
        )
        text += f"\n\nOptions:\n{opts}"
    return text


def _format_error_dm(lead: str, payload: dict[str, Any]) -> str:
    error = html.escape(str(payload.get("error", "")), quote=False)
    context = html.escape(str(payload.get("context", "")), quote=False)
    traceback = payload.get("traceback", "")
    recoverable = payload.get("recoverable", True)
    lead_e = html.escape(lead, quote=False)
    text = (
        f"<b>[ERROR] Lead <code>{lead_e}</code>:</b> {error}\n"
        f"Context: {context}"
    )
    if traceback:
        tb_snippet = html.escape(str(traceback)[:500], quote=False)
        text += f"\n<pre>{tb_snippet}</pre>"
    if not recoverable:
        text += "\n\n<i>Lead has stopped — manual intervention may be needed.</i>"
    return text


def _deliver_event(event_id: int, lead: str, type_: str, payload_json: str) -> None:
    """Trigger DM delivery for a single event. Updates delivered_at or delivery_error."""
    try:
        payload: dict[str, Any] = json.loads(payload_json)
    except (json.JSONDecodeError, ValueError):
        _store.mark_delivery_error(event_id, "invalid payload_json")
        return

    try:
        if type_ == "STARTED":
            text = _format_started_dm(lead, payload)
            msg_id = _send_proactive_dm(text)

        elif type_ == "MILESTONE":
            with _milestone_lock:
                last_ts = _milestone_last_dmed.get(lead, 0.0)
                now = time.time()
                if now - last_ts < _MILESTONE_THROTTLE_SECS:
                    # Within throttle window — store but don't DM yet
                    return
                _milestone_last_dmed[lead] = now
            text = _format_milestone_dm(lead, [_store.get_event(event_id) or {}])
            msg_id = _send_proactive_dm(text)

        elif type_ == "COMPLETED":
            # Flush any accumulated undelivered milestones first
            pending_ms = _store.get_undelivered_milestones(lead)
            if pending_ms:
                ms_text = _format_milestone_dm(lead, pending_ms)
                _send_proactive_dm(ms_text)
                for ms in pending_ms:
                    _store.mark_delivered(ms["id"])
                with _milestone_lock:
                    _milestone_last_dmed.pop(lead, None)
            text, files = _format_completed_dm(lead, payload)
            msg_id = _send_proactive_dm(text, files if files else None)

        elif type_ == "NEEDS_INPUT":
            text = _format_needs_input_dm(lead, payload)
            msg_id = _send_proactive_dm(text)
            if msg_id is not None:
                # Store tg_msg_id in pending_inputs for correlation fallback
                with _store._lock:
                    _store._conn.execute(
                        "UPDATE pending_inputs SET tg_msg_id = ? WHERE event_id = ? AND status = 'open'",
                        (msg_id, event_id),
                    )

        elif type_ == "ERROR":
            text = _format_error_dm(lead, payload)
            msg_id = _send_proactive_dm(text)

        else:
            return

        if msg_id is not None or type_ not in URGENT_TYPES:
            _store.mark_delivered(event_id)
        else:
            _store.mark_delivery_error(event_id, "DM delivery returned None")

    except Exception as exc:
        err_str = str(exc)[:500]
        _store.mark_delivery_error(event_id, err_str)
        _log_notify_error(f"deliver_event({event_id}) {type_}: {err_str}")


def _drain_on_startup() -> None:
    """Re-deliver any undelivered urgent events from before a restart."""
    try:
        rows = _store.get_undelivered_urgent()
        if not rows:
            return
        for row in rows:
            _deliver_event(
                row["id"], row["lead"], row["type"], row["payload_json"]
            )
    except Exception as exc:
        _log_notify_error(f"drain_on_startup failed: {exc}")


def _timeout_monitor_loop() -> None:
    """Background loop that closes NEEDS_INPUT rows whose timeout has expired.

    Runs every 30 seconds. For each expired pending_input, marks the row as
    timed_out and sends a DM to the user (per Phase-11 resolution option c).
    """
    _POLL_SECS = 30
    while True:
        time.sleep(_POLL_SECS)
        try:
            rows = _store.get_open_pending_inputs(limit=50)
            now = time.time()
            for row in rows:
                timeout_secs = row.get("timeout_secs")
                if not timeout_secs:
                    continue
                created_at = float(row.get("created_at") or 0)
                if now - created_at < timeout_secs:
                    continue
                # Timeout expired — mark timed_out and DM
                event_id = int(row["event_id"])
                lead = row["lead"]
                marked = _store.mark_pending_timed_out(event_id)
                if not marked:
                    continue  # already resolved by another path
                text = (
                    f"<b>Lead <code>{lead}</code> timed out</b> waiting for your input.\n"
                    f"Question: {row['question']}\n"
                    f"The lead has proceeded without an answer. "
                    f"Reply or re-send your answer via the bot if needed."
                )
                _send_proactive_dm(text)
        except Exception as exc:
            _log_notify_error(f"timeout_monitor: {exc}")


# ---- Notify HTTP request handler -----------------------------------------

class _NotifyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the notify listener."""

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # silence BaseHTTPRequestHandler's stderr logging

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length > _MAX_PAYLOAD_BYTES:
            self._respond(413, {"error": "payload too large"})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid JSON"})
            return None

    def _respond(self, code: int, body: dict, extra_headers: dict | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ok", "listener": "running"})
        elif self.path.startswith("/events"):
            self._handle_events()
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/notify":
            self._handle_notify()
        elif self.path == "/resolve_pending_input":
            self._handle_resolve()
        elif self.path == "/mark_read":
            self._handle_mark_read()
        else:
            self._respond(404, {"error": "not found"})

    def _handle_notify(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        event_id = body.get("event_id")
        lead = body.get("lead", "")
        type_ = body.get("type", "")
        payload_json = body.get("payload_json", "")

        if not event_id or not lead or not type_ or not payload_json:
            self._respond(400, {"error": "missing required fields: event_id, lead, type, payload_json"})
            return
        if type_ not in VALID_TYPES:
            self._respond(400, {"error": f"unknown type {type_!r}"})
            return

        # Deliver in a background thread so the POST returns quickly
        t = threading.Thread(
            target=_deliver_event,
            args=(event_id, lead, type_, payload_json),
            daemon=True,
        )
        t.start()
        self._respond(202, {"event_id": event_id, "queued": True})

    def _handle_resolve(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        event_id = body.get("event_id")
        answer = body.get("answer", "")
        if not event_id:
            self._respond(400, {"error": "missing event_id"})
            return
        resolved = _store.mark_pending_resolved(int(event_id), answer)
        self._respond(200, {"resolved": resolved})

    def _handle_mark_read(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        event_ids = body.get("event_ids", [])
        if not isinstance(event_ids, list):
            self._respond(400, {"error": "event_ids must be a list"})
            return
        _store.mark_hook_injected([int(i) for i in event_ids if isinstance(i, int)])
        self._respond(200, {"marked": len(event_ids)})

    def _handle_events(self) -> None:
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        lead = qs.get("lead", [None])[0]
        limit_str = qs.get("limit", ["20"])[0]
        unread_only = qs.get("unread_only", ["false"])[0].lower() == "true"
        try:
            limit = min(int(limit_str), 100)
        except ValueError:
            limit = 20

        if unread_only:
            rows = _store.get_uninjected(limit=limit)
        else:
            rows = _store.get_recent(lead=lead, limit=limit)

        open_inputs = _store.get_open_pending_inputs(limit=5)
        self._respond(200, {"events": rows, "open_pending_inputs": open_inputs})


def _start_notify_listener() -> None:
    port = int(os.environ.get("HERMES_NOTIFY_PORT", str(_NOTIFY_PORT_DEFAULT)))
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _NotifyHandler)
        server.serve_forever()
    except Exception as exc:
        _log_notify_error(f"notify listener failed to start: {exc}")


# ---- Unix-socket bridge for the FastAPI dashboard backend -----------------

async def _h_list_sessions(_p: dict) -> dict:
    return {"items": claude_state.list_sessions()}


async def _h_read_activity(p: dict) -> dict:
    return {"items": claude_state.read_activity_log(p.get("limit", 200))}


async def _h_read_memory(p: dict) -> dict:
    return {"text": claude_state.read_memory(p["project_slug"])}


async def _h_list_threads(p: dict) -> dict:
    return {"items": claude_state.list_transcript_threads(p.get("limit", 50))}


async def _h_read_transcript(p: dict) -> dict:
    return {"items": claude_state.read_transcript(p["thread_id"], p["project"])}


HANDLERS = {
    "list_sessions": _h_list_sessions,
    "read_activity_log": _h_read_activity,
    "read_memory": _h_read_memory,
    "list_threads": _h_list_threads,
    "read_transcript": _h_read_transcript,
}


def _start_socket_server() -> None:
    serve_blocking(HANDLERS)


def main() -> None:
    global _store, _milestone_last_dmed

    # Initialise the event store (creates tables if absent)
    _store = EventStore()

    # Seed the MILESTONE throttle dict from the DB so a fresh restart doesn't
    # re-DM milestones that were already delivered before the restart.
    _milestone_last_dmed = _store.get_milestone_last_delivered_times()

    # Run the unix socket server in a background thread so the FastAPI bridge
    # can call into our state without going through MCP.
    t_socket = threading.Thread(target=_start_socket_server, daemon=True)
    t_socket.start()

    # Run the notify HTTP listener in a background thread (same pattern).
    t_listener = threading.Thread(target=_start_notify_listener, daemon=True)
    t_listener.start()

    # Drain any undelivered urgent events from before the last restart.
    t_drain = threading.Thread(target=_drain_on_startup, daemon=True)
    t_drain.start()

    # Monitor for NEEDS_INPUT rows whose timeout has expired.
    t_timeout = threading.Thread(target=_timeout_monitor_loop, daemon=True)
    t_timeout.start()

    # Prewarm the routines cloud cache so the first dashboard hit is fast.
    t_prewarm = threading.Thread(target=_prewarm_routines_cache, daemon=True)
    t_prewarm.start()

    mcp.run()


if __name__ == "__main__":
    main()
