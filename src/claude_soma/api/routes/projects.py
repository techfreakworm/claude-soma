from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.project_orchestrator.server import (
    get_status_impl,
    get_team_impl,
    kill_project_impl,
    list_projects_impl,
    send_to_project_impl,
)


router = APIRouter(prefix="/projects", dependencies=[Depends(require_authed_user)])


@router.get("")
def list_projects() -> list[dict]:
    return list_projects_impl()


@router.get("/{name}")
def project_detail(name: str) -> dict:
    try:
        return get_status_impl(name)
    except RuntimeError:
        raise HTTPException(status_code=404, detail=f"project {name!r} not found")


@router.get("/{name}/team")
def project_team(name: str) -> dict:
    """Live agent-team roster for a project-lead (teammates in its tmux panes)."""
    try:
        return get_team_impl(name)
    except RuntimeError:
        raise HTTPException(status_code=404, detail=f"project {name!r} not found")


@router.post("/{name}/message")
def send_message(name: str, body: dict) -> dict:
    try:
        return send_to_project_impl(name=name, message=body.get("message", ""))
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{name}/kill")
def kill(name: str) -> dict:
    try:
        return kill_project_impl(name=name, archive=True)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
