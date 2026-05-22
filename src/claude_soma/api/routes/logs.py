from __future__ import annotations

from fastapi import APIRouter, Depends

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.hermes_api.claude_state import read_activity_log


router = APIRouter(prefix="/logs", dependencies=[Depends(require_authed_user)])


@router.get("")
def list_logs(limit: int = 200, tool: str | None = None) -> list[dict]:
    rows = read_activity_log(limit=limit * 2 if tool else limit)
    if tool:
        rows = [r for r in rows if r.get("tool") == tool][:limit]
    else:
        rows = rows[-limit:]
    return rows
