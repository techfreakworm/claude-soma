# src/claude_soma/mcp_servers/project_orchestrator/server.py
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .registry import Registry
from .spawner import (
    spawn_background_lead, kill_session, is_lead_alive, discover_team,
)
from .templates import load_template, list_template_names, TemplateNotFound

logger = logging.getLogger(__name__)


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


def _reconcile_active() -> list[dict]:
    """Return the registry rows that are marked active AND whose lead is really
    still running, flipping any active-but-dead row to 'dead' as a side effect.

    The registry only ever learns a lead died if kill_project was called; a lead
    that vanished on its own (channel restart before cgroup isolation, crash, or
    a finished task) stayed 'active' forever. This cross-checks the live tmux
    session so list_projects, the concurrency gate, and get_status agree on what
    'active' means. Uses bump_activity=False so the demotion doesn't reset the
    idle clock.
    """
    live: list[dict] = []
    for r in _reg().list_active():
        if is_lead_alive(r["name"]):
            live.append(r)
        else:
            _reg().set_status(r["name"], "dead", bump_activity=False)
    return live


def spawn_project_impl(
    *, name: str, type_: str, brief: str, permission_mode: str = "acceptEdits"
) -> dict:
    # Reconcile first so ghost leads (dead but still 'active' in the registry)
    # don't wrongly count against the concurrency cap and block a real spawn.
    active = _reconcile_active()
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
    rows = _reconcile_active()
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


def touch_project_impl(*, name: str) -> dict:
    """Bump last_activity for a lead that the bot messaged via raw tmux send-keys.

    send_to_project_impl touches the registry automatically, but the bot's
    canonical chat-to-lead path is raw `tmux send-keys` (which bypasses
    send_to_project_impl entirely). Validated 2026-05-29: t1-spawn-test's
    last_activity stayed at spawn timestamp despite a delivered+answered message.
    Call this right after every tmux send-keys to a lead so the idle clock
    reflects real conversation activity.
    """
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    _reg().touch(name)
    return {"name": name, "touched_at": time.time()}


def kill_project_impl(*, name: str, archive: bool = True) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    # kill_session stops the systemd unit then the tmux session (both best-effort
    # internally). We verify post-kill liveness and retry once before accepting
    # the result: silent subprocess swallows inside kill_session can leave the
    # lead alive while the registry would show it as killed, hiding zombie leads.
    # If the lead survives both kill attempts we raise instead of flipping the
    # registry, so the caller can investigate and retry.
    kill_session(p["agent_id"])
    if is_lead_alive(p["agent_id"]):
        kill_session(p["agent_id"])
        if is_lead_alive(p["agent_id"]):
            agent_id = p["agent_id"]
            bare = agent_id[len("soma-proj-"):] if agent_id.startswith("soma-proj-") else agent_id
            raise RuntimeError(
                f"kill_project: agent {agent_id!r} is still alive after two kill attempts; "
                f"check tmux socket soma-lead-{bare} and unit claude-soma-lead-{bare}.service"
            )
    _reg().set_status(name, "killed")
    if archive:
        memory_dir = Path(p["cwd"]) / ".claude"
        if not memory_dir.exists():
            logger.warning("nothing to archive for %s", name)
    return {"name": name, "killed_at": time.time()}


def get_status_impl(name: str) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    # Reconcile: if the registry thinks it's active but the tmux session is
    # gone, the lead vanished -- report (and persist) 'dead' rather than lie.
    status = p["status"]
    if status == "active" and not is_lead_alive(name):
        _reg().set_status(name, "dead", bump_activity=False)
        status = "dead"
    return {
        "name": p["name"], "agent_id": p["agent_id"], "type": p["type"],
        "cwd": p["cwd"], "rc_url": p["rc_url"], "status": status,
        "spawned_at": p["spawned_at"],
        "idle_for_seconds": _reg().idle_for(name),
    }


def get_team_impl(name: str) -> dict:
    """Return a lead's live agent-team roster (teammates discovered from its tmux
    panes). Raises if there's no such project."""
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    return {"name": p["name"], "team": discover_team(p["agent_id"])}


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
def touch_project(name: str) -> dict:
    """Bump last_activity for a lead messaged via raw tmux send-keys.

    The bot's canonical chat-to-lead path is raw `tmux send-keys`, which
    bypasses send_to_project's automatic touch. Call this right after every
    tmux send-keys to a lead to keep the idle clock accurate.
    """
    return touch_project_impl(name=name)


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
