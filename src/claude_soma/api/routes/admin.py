from __future__ import annotations

import json
import os
import time
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


@router.post("/broadcast")
def broadcast(body: BroadcastBody) -> dict:
    # The channel session picks up broadcast requests by polling
    # the broadcast queue (set up in Task 36).
    queue = Path(os.environ.get(
        "HERMES_BROADCAST_QUEUE", "/opt/claude-soma/broadcast.jsonl"
    ))
    queue.parent.mkdir(parents=True, exist_ok=True)
    with queue.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "message": body.message}) + "\n")
    return {"queued_at": time.time()}


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
