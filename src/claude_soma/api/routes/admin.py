from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.project_orchestrator.server import (
    kill_project_impl,
    list_projects_impl,
)


router = APIRouter(prefix="/admin", dependencies=[Depends(require_authed_user)])


class BroadcastBody(BaseModel):
    message: str


def _send_telegram(message: str) -> tuple[bool, str | None]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = (
        os.environ.get("TELEGRAM_CHAT_ID")
        or os.environ.get("HERMES_NOTIFY_CHAT_ID")
        or os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    )
    if not token or not chat_id:
        return False, "missing TELEGRAM_BOT_TOKEN or chat_id env"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


@router.post("/broadcast")
def broadcast(body: BroadcastBody) -> dict:
    queue = Path(os.environ.get(
        "HERMES_BROADCAST_QUEUE", "/opt/claude-soma/broadcast.jsonl"
    ))
    queue.parent.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    with queue.open("a") as f:
        f.write(json.dumps({"ts": ts, "message": body.message}) + "\n")

    # Discord primary, Telegram best-effort fallback (see claude_soma.operator_dm).
    tg_error: dict[str, str | None] = {}

    def _telegram_send() -> int | None:
        ok, err = _send_telegram(body.message)
        if not ok:
            tg_error["error"] = err
        return 1 if ok else None

    from claude_soma.operator_dm import send_operator_dm

    mid = send_operator_dm(body.message, is_html=False, telegram_fallback=_telegram_send)
    delivered = mid is not None
    error = None if delivered else tg_error.get("error", "discord and telegram both failed")
    return {"queued_at": ts, "delivered": delivered, "error": error}


@router.post("/pause-all")
def pause_all() -> dict:
    active = list_projects_impl()
    killed = []
    for p in active:
        try:
            kill_project_impl(name=p["name"], archive=True)
            killed.append(p["name"])
        except RuntimeError:
            continue
    return {"paused_count": len(killed), "names": killed}
