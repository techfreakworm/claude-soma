from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.hermes_api.claude_state import read_memory
from claude_soma.mcp_servers.project_orchestrator.registry import Registry


router = APIRouter(prefix="/memory", dependencies=[Depends(require_authed_user)])


@router.get("/{project}")
def get_memory(project: str) -> dict:
    cwd: str | None = None
    try:
        reg = Registry(os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite"))
        row = reg.get(project)
        cwd = row.get("cwd") if row else None
    except Exception:
        cwd = None
    return read_memory(project, cwd=cwd)
