from __future__ import annotations

import json
import mimetypes
import os
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import claude_state
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
    # Run the unix socket server in a background thread so the FastAPI bridge
    # can call into our state without going through MCP.
    t = threading.Thread(target=_start_socket_server, daemon=True)
    t.start()
    mcp.run()


if __name__ == "__main__":
    main()
