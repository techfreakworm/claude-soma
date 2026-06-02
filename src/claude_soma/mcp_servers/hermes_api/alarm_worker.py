from __future__ import annotations

# ENV KNOBS (read once at run_alarm_loop startup):
#   HERMES_ALARM_POLL_SECONDS             default 600   — seconds between ticks
#   HERMES_ALARM_CONTEXT_THRESHOLD_TOKENS default 150000 — fire if est_tokens >= this
#   HERMES_ALARM_DEBOUNCE_SECONDS         default 3600  — min seconds between alarms per lead
#   TELEGRAM_BOT_TOKEN                    required; also read from ~/.claude/channels/telegram/.env
#   HERMES_NOTIFY_CHAT_ID / TELEGRAM_OPERATOR_CHAT_ID / TELEGRAM_CHAT_ID — DM target chat id
#   All of the above must exist in /etc/claude-soma/secrets.env on the VPS; never committed here.

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

_last_alarm: dict[str, float] = {}

_NOTIFY_CHAT_ID_DEFAULT = "935376085"
_TG_ENV_FILE = Path.home() / ".claude" / "channels" / "telegram" / ".env"
_TG_API_BASE = "https://api.telegram.org"


def _alarm_load_tg_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    try:
        for line in _TG_ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _, val = stripped.partition("=")
                if key.strip() == "TELEGRAM_BOT_TOKEN":
                    return val.strip()
    except OSError:
        pass
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN not set in env and not found in "
        f"{_TG_ENV_FILE}"
    )


def _alarm_chat_id() -> str:
    return os.environ.get(
        "HERMES_NOTIFY_CHAT_ID",
        os.environ.get(
            "TELEGRAM_OPERATOR_CHAT_ID",
            os.environ.get("TELEGRAM_CHAT_ID", _NOTIFY_CHAT_ID_DEFAULT),
        ),
    )


def _send_alarm_dm(text: str) -> None:
    try:
        token = _alarm_load_tg_token()
        chat_id = _alarm_chat_id()
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"{_TG_API_BASE}/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception:
        pass


def _run_alarm_tick(
    active_leads: list[dict[str, Any]],
    estimate_fn: Callable[[str], int],
    send_fn: Callable[[str], None],
    last_alarm: dict[str, float],
    threshold: int,
    debounce: float,
    now: float | None = None,
) -> None:
    if now is None:
        now = time.time()
    for lead in active_leads:
        name = lead["name"]
        est = estimate_fn(name)
        if est < threshold:
            continue
        last_fire = last_alarm.get(name, 0.0)
        if now - last_fire <= debounce:
            continue
        msg = (
            f"Lead `{name}` at ~{est} estimated tokens — "
            "consider /clear or fresh spawn before next turn. (Auto-alarm; hush 1h.)"
        )
        send_fn(msg)
        last_alarm[name] = now


def run_alarm_loop() -> None:
    poll_secs = int(os.environ.get("HERMES_ALARM_POLL_SECONDS", "600"))
    threshold = int(os.environ.get("HERMES_ALARM_CONTEXT_THRESHOLD_TOKENS", "150000"))
    debounce = float(os.environ.get("HERMES_ALARM_DEBOUNCE_SECONDS", "3600"))
    db_path = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")

    try:
        from claude_soma.mcp_servers.project_orchestrator.registry import Registry  # noqa: PLC0415
        from claude_soma.mcp_servers.project_orchestrator.spawner import (  # noqa: PLC0415
            _estimate_context_tokens,
        )
        reg = Registry(db_path)
    except Exception:
        return

    while True:
        time.sleep(poll_secs)
        try:
            active = reg.list_active()
            _run_alarm_tick(
                active_leads=active,
                estimate_fn=_estimate_context_tokens,
                send_fn=_send_alarm_dm,
                last_alarm=_last_alarm,
                threshold=threshold,
                debounce=debounce,
            )
        except Exception:
            pass
