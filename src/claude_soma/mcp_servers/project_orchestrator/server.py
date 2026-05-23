# src/claude_soma/mcp_servers/project_orchestrator/server.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .registry import Registry
from .spawner import (
    spawn_background_lead, kill_session, InvalidProjectName, BriefTooLong,
)
from .templates import load_template, list_template_names, TemplateNotFound


DB_PATH = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
PROJECTS_ROOT = os.environ.get("HERMES_PROJECTS_ROOT", "/home/ubuntu/projects")
MAX_CONCURRENT = int(os.environ.get("HERMES_MAX_CONCURRENT_PROJECTS", "6"))


_registry: Registry | None = None


def _reg() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(os.environ.get("HERMES_ORCH_DB", DB_PATH))
    return _registry


def _reset_singletons_for_tests() -> None:
    global _registry
    _registry = None


def _resolve_template(type_: str) -> dict:
    try:
        return load_template(type_)
    except TemplateNotFound:
        return load_template("custom")


def spawn_project_impl(
    *, name: str, type_: str, brief: str, permission_mode: str = "acceptEdits"
) -> dict:
    active = _reg().list_active()
    if len(active) >= MAX_CONCURRENT:
        raise RuntimeError(
            f"already at concurrency cap ({MAX_CONCURRENT}); "
            f"kill one project first. Active: {[p['name'] for p in active]}"
        )
    tmpl = _resolve_template(type_)
    projects_root = os.environ.get("HERMES_PROJECTS_ROOT", PROJECTS_ROOT)
    cwd = Path(projects_root) / name
    composed_brief = tmpl.get("default_brief", "") + "\n\n" + brief

    spawn = spawn_background_lead(
        name=name, brief=composed_brief, cwd=cwd,
        permission_mode=permission_mode or tmpl.get("permission_mode", "acceptEdits"),
    )
    _reg().register(
        name, agent_id=spawn["agent_id"], type_=tmpl["type"],
        cwd=str(cwd), rc_url=spawn.get("rc_url"),
        permission_mode=permission_mode, brief=composed_brief,
    )
    return {
        "agent_id": spawn["agent_id"],
        "rc_url": spawn.get("rc_url", ""),
        "cwd": str(cwd),
        "type": tmpl["type"],
    }


def list_projects_impl() -> list[dict]:
    rows = _reg().list_active()
    now = time.time()
    return [
        {
            "name": r["name"], "agent_id": r["agent_id"], "type": r["type"],
            "cwd": r["cwd"], "rc_url": r["rc_url"], "status": r["status"],
            "spawned_at": r["spawned_at"],
            "idle_for_seconds": max(0.0, now - float(r["last_activity"])),
        }
        for r in rows
    ]


def send_to_project_impl(*, name: str, message: str) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    _reg().touch(name)
    return {"name": name, "agent_id": p["agent_id"], "sent_at": time.time()}


def kill_project_impl(*, name: str, archive: bool = True) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    # Terminate the tmux-wrapped claude session first (it's the actual running
    # process); then mark the registry row as killed so list_active hides it.
    # kill_session is best-effort — if the tmux session is already gone we
    # still want to update the registry.
    kill_session(p["agent_id"])
    _reg().set_status(name, "killed")
    return {"name": name, "killed_at": time.time()}


def get_status_impl(name: str) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    return {
        "name": p["name"], "agent_id": p["agent_id"], "type": p["type"],
        "cwd": p["cwd"], "rc_url": p["rc_url"], "status": p["status"],
        "spawned_at": p["spawned_at"],
        "idle_for_seconds": _reg().idle_for(name),
    }


mcp = FastMCP("project_orchestrator")


@mcp.tool()
def spawn_project(
    name: str, type: str, brief: str, permission_mode: str = "acceptEdits"
) -> dict:
    """Spawn a new project-lead background session with its own team scaffold."""
    return spawn_project_impl(
        name=name, type_=type, brief=brief, permission_mode=permission_mode
    )


@mcp.tool()
def list_projects() -> list[dict]:
    """List active project-leads with their status and Remote Control URLs."""
    return list_projects_impl()


@mcp.tool()
def send_to_project(name: str, message: str) -> dict:
    """Resolve a project name to its agent_id so the caller can SendMessage."""
    return send_to_project_impl(name=name, message=message)


@mcp.tool()
def kill_project(name: str, archive: bool = True) -> dict:
    """Mark a project as killed; reaper will gracefully shut it down."""
    return kill_project_impl(name=name, archive=archive)


@mcp.tool()
def get_status(name: str) -> dict:
    """Return current status of a project-lead."""
    return get_status_impl(name)


@mcp.tool()
def list_template_types() -> list[str]:
    """Return the available project template type names."""
    return list_template_names()


@mcp.tool()
def register_routine(
    name: str,
    kind: str,
    schedule: str,
    target_skill: str = "",
    description: str = "",
    created_by: str = "bot",
    metadata_json: str = "",
) -> dict:
    """Register a routine in the local registry so it appears in /api/routines.

    Call this after creating a cloud routine via RemoteTrigger.create() OR
    after installing a local systemd timer."""
    meta = json.loads(metadata_json) if metadata_json else None
    _reg().register_routine(
        name,
        kind=kind,
        schedule=schedule,
        target_skill=target_skill or None,
        description=description or None,
        created_by=created_by,
        metadata=meta,
    )
    return {"registered": name, "kind": kind}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
