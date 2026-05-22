from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from claude_soma.api.auth import require_authed_user
from claude_soma.api.bridge import call_hermes


router = APIRouter(prefix="/conversations", dependencies=[Depends(require_authed_user)])


@router.get("")
async def list_conversations() -> list[dict]:
    r = await call_hermes("list_threads", {"limit": 50})
    return r.get("items", [])


@router.get("/{thread_id}")
async def read_thread(thread_id: str, project: str = "") -> list[dict]:
    if not project:
        raise HTTPException(status_code=400, detail="?project=<slug> required")
    r = await call_hermes(
        "read_transcript", {"thread_id": thread_id, "project": project}
    )
    return r.get("items", [])
