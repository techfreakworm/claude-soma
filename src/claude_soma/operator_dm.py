"""Central operator-DM helper: Discord primary, Telegram best-effort fallback.

Every proactive operator-notification path in claude-soma routes through this
helper so a single module owns the dual-route policy. Discord is the primary
route — Telegram is banned in India (~2026-06) and its sendMessage returns
HTTP 000 — and Telegram stays a best-effort fallback so delivery auto-resumes
when the ban lifts.

Design contract:
  - Never raises. A notification failure must never break its caller.
  - Tokens are read at call time from the process env or, failing that, from
    /etc/claude-soma/secrets.env (Discord) / the discord plugin .env. They are
    NEVER logged.
  - Timeouts on every network call so a dead route can't hang the caller.

Kill switches (env):
  SOMA_DISCORD_DM_DISABLED=1   -> skip the Discord route (Telegram only)
  SOMA_TELEGRAM_DM_DISABLED=1  -> skip the Telegram fallback (Discord only)

Public API:
  send_operator_dm(text, files=None, *, telegram_fallback=None, is_html=True,
                   timeout=15) -> int | None
      Returns a delivered message id (Discord snowflake when Discord wins, or
      whatever telegram_fallback returns) or None if every route failed.
"""

from __future__ import annotations

import html as _html
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

_DISCORD_API_BASE = os.environ.get("SOMA_DISCORD_API_BASE", "https://discord.com/api/v10")
_DISCORD_DM_CHANNEL_ID_DEFAULT = "1516423259699155045"
_SECRETS_ENV = "/etc/claude-soma/secrets.env"
_DISCORD_PLUGIN_ENV = str(Path.home() / ".claude" / "channels" / "discord" / ".env")

_DISCORD_CONTENT_LIMIT = 2000  # Discord per-message content cap
_DISCORD_MAX_UPLOAD = 25 * 1024 * 1024  # conservative bot upload cap
_DEFAULT_TIMEOUT = 15

# Discord's API (behind Cloudflare) rejects requests with a library-default
# User-Agent (urllib -> HTTP 403, Cloudflare code 1010) and the API docs require
# bots to send a descriptive UA. Without this header every Discord POST fails.
_DISCORD_USER_AGENT = "DiscordBot (https://github.com/techfreakworm/claude-soma, 0.1.0)"

_NOTIFY_LOG = Path("/var/log/claude-soma/operator-dm.log")


def _log(msg: str) -> None:
    """Append a line to the operator-dm log. Never raises; never logs secrets."""
    try:
        _NOTIFY_LOG.parent.mkdir(parents=True, exist_ok=True)
        import time

        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _NOTIFY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {msg}\n")
    except OSError:
        pass


def _discord_channel_id() -> str:
    return os.environ.get("SOMA_DISCORD_DM_CHANNEL_ID", _DISCORD_DM_CHANNEL_ID_DEFAULT)


def _kill_switch_enabled(name: str) -> bool:
    """True if `name` is set to "1" in the process env OR, failing that, in
    /etc/claude-soma/secrets.env.

    Long-lived leads / listeners capture their env at spawn time. A kill switch
    (e.g. SOMA_DISCORD_DM_DISABLED) added to secrets.env AFTER spawn would never
    reach the process env, so a stale-env process would ignore it. Falling back
    to a live read of secrets.env makes the switch honour the current on-disk
    value at send-time regardless of when the process was started. Mirrors the
    shell notify_lib.sh / neet common.sh secrets.env fallback. Never raises.
    """
    if os.environ.get(name) == "1":
        return True
    return _read_var_from_file(_SECRETS_ENV, name) == "1"


def _read_var_from_file(path: str, name: str) -> str:
    """Return the last `name=value` from an env-style file, quotes stripped."""
    try:
        with open(path, encoding="utf-8") as fh:
            last = ""
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                key, _, val = s.partition("=")
                if key.strip() != name:
                    continue
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                last = val
            return last
    except OSError:
        return ""


def _load_discord_token() -> str:
    """env DISCORD_BOT_TOKEN -> discord plugin .env -> secrets.env. Never logged."""
    tok = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if tok:
        return tok
    for path in (_DISCORD_PLUGIN_ENV, _SECRETS_ENV):
        tok = _read_var_from_file(path, "DISCORD_BOT_TOKEN")
        if tok:
            return tok
    return ""


_A_RE = re.compile(r'<a\s+href="([^"]*)"\s*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_discord(text: str) -> str:
    """Best-effort translate Telegram-HTML DM text into Discord markdown.

    <a href="u">t</a> -> "t (u)"; <b>/<strong> -> **; <i>/<em> -> *;
    <pre> -> ```; <code> -> `; remaining tags stripped; HTML entities unescaped.
    """
    t = _A_RE.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    t = re.sub(r"</?(?:b|strong)>", "**", t, flags=re.IGNORECASE)
    t = re.sub(r"</?(?:i|em)>", "*", t, flags=re.IGNORECASE)
    t = re.sub(r"</?pre>", "```", t, flags=re.IGNORECASE)
    t = re.sub(r"</?code>", "`", t, flags=re.IGNORECASE)
    t = _TAG_RE.sub("", t)
    return _html.unescape(t)


def _chunk(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text else [""]
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def _discord_post_content(token: str, content: str, timeout: int) -> int | None:
    url = f"{_DISCORD_API_BASE}/channels/{_discord_channel_id()}/messages"
    body = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": _DISCORD_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if 200 <= resp.status < 300:
            try:
                data = json.loads(resp.read())
                mid = data.get("id")
                return int(mid) if mid else 1
            except (ValueError, TypeError, json.JSONDecodeError):
                return 1
    return None


def _discord_post_file(token: str, file_path: str, timeout: int) -> int | None:
    path = Path(file_path)
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"
    boundary = uuid.uuid4().hex
    payload_json = json.dumps(
        {"content": "", "attachments": [{"id": 0, "filename": path.name}]}
    )
    parts = [
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="payload_json"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{payload_json}\r\n"
        ).encode(),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files[0]"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        + data
        + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    url = f"{_DISCORD_API_BASE}/channels/{_discord_channel_id()}/messages"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bot {token}",
            "User-Agent": _DISCORD_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(timeout, 30)) as resp:
        if 200 <= resp.status < 300:
            try:
                return int(json.loads(resp.read()).get("id") or 1)
            except (ValueError, TypeError, json.JSONDecodeError):
                return 1
    return None


def _send_via_discord(text: str, files: list[str] | None, is_html: bool, timeout: int) -> int | None:
    token = _load_discord_token()
    if not token:
        _log("discord route skipped: no DISCORD_BOT_TOKEN")
        return None
    content = _html_to_discord(text) if is_html else text
    last_id: int | None = None
    notes: list[str] = []
    for fp in files or []:
        try:
            if Path(fp).stat().st_size > _DISCORD_MAX_UPLOAD:
                notes.append(f"[file too large for Discord upload: {fp}]")
        except OSError:
            pass
    if notes:
        content = content + "\n\n" + "\n".join(notes)
    for chunk in _chunk(content, _DISCORD_CONTENT_LIMIT):
        mid = _discord_post_content(token, chunk, timeout)
        if mid is None:
            return None
        last_id = mid
    for fp in files or []:
        try:
            if Path(fp).stat().st_size > _DISCORD_MAX_UPLOAD:
                continue
            mid = _discord_post_file(token, fp, timeout)
            if mid is not None:
                last_id = mid
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            _log(f"discord file upload failed for {Path(fp).name}: {type(exc).__name__}")
    return last_id


def send_operator_dm(
    text: str,
    files: list[str] | None = None,
    *,
    telegram_fallback: Callable[[], int | None] | None = None,
    is_html: bool = True,
    timeout: int = _DEFAULT_TIMEOUT,
) -> int | None:
    """Deliver an operator DM: Discord primary, Telegram best-effort fallback.

    Args:
        text: message body. When is_html=True it is treated as Telegram-HTML and
              translated to Discord markdown for the Discord route.
        files: optional absolute paths to attach (Discord upload best-effort;
               the telegram_fallback handles its own attachments).
        telegram_fallback: zero-arg callable that performs the legacy Telegram
            send and returns a message id (or None). Invoked only if Discord is
            disabled or fails. It must not raise (exceptions are swallowed).
        is_html: whether `text` is Telegram-HTML (default True).
        timeout: per-request timeout in seconds.

    Returns the delivered message id, or None if all routes failed. Never raises.
    """
    if not _kill_switch_enabled("SOMA_DISCORD_DM_DISABLED"):
        try:
            mid = _send_via_discord(text, files, is_html, timeout)
            if mid is not None:
                return mid
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            _log(f"discord route failed: {type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 -- notify must never raise
            _log(f"discord route failed (unexpected): {type(exc).__name__}")

    if _kill_switch_enabled("SOMA_TELEGRAM_DM_DISABLED"):
        return None
    if telegram_fallback is None:
        return None
    try:
        return telegram_fallback()
    except Exception as exc:  # noqa: BLE001 -- notify must never raise
        _log(f"telegram fallback failed: {type(exc).__name__}")
        return None
