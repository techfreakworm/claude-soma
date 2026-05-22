from __future__ import annotations

from fastapi import APIRouter, Depends

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.hermes_api.claude_state import read_memory


router = APIRouter(prefix="/memory", dependencies=[Depends(require_authed_user)])


@router.get("/{project}")
def get_memory(project: str) -> dict:
    return {"project": project, "text": read_memory(project)}
