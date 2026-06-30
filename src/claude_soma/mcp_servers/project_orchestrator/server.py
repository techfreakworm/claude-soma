# src/claude_soma/mcp_servers/project_orchestrator/server.py
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
from datetime import date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .registry import Registry
from .spawner import (
    spawn_background_lead, resume_background_lead, kill_session,
    is_lead_alive, lead_liveness, discover_team,
    load_hosts, _host_cfg,
    LEAD_SOCKET_PREFIX, TMUX_SESSION_PREFIX,
)
from .templates import load_template, list_template_names, TemplateNotFound

logger = logging.getLogger(__name__)

_NOTIFY_CONVENTION_PATH = (
    Path(__file__).parents[4] / "system_prompts" / "lead_notify_convention.md"
)
_NOTIFY_CONVENTION: str = _NOTIFY_CONVENTION_PATH.read_text()

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
        host = r.get("host", "local")
        # Local: bool liveness (today's behavior, conservative on tool error).
        # Remote: tri-state so a momentarily-unreachable host is never demoted to
        # 'dead' (only a confirmed-dead remote session is demoted).
        if host == "local":
            keep = is_lead_alive(r["name"], host)
        else:
            keep = lead_liveness(r["name"], host) != "dead"
        if keep:
            live.append(r)
        else:
            _reg().set_status(r["name"], "dead", bump_activity=False)
    return live


def _host_cap(host: str) -> int:
    return int(_host_cfg(host).get("max_concurrent", MAX_CONCURRENT))


def _admit_or_raise(host: str, tier: str, active: list[dict]) -> None:
    """Per-host concurrency + memory admission. Raises RuntimeError if refused."""
    on_host = [p for p in active if p.get("host", "local") == host]
    cap = _host_cap(host)
    if len(on_host) >= cap:
        raise RuntimeError(
            f"host {host!r} at concurrency cap ({cap}); "
            f"active there: {[p['name'] for p in on_host]}"
        )
    cfg = _host_cfg(host)
    caps = cfg.get("tier_caps")
    if caps and cfg.get("ram_mb"):
        used = sum(
            caps.get(p.get("tier", "standard"), {}).get("max_mb", 0) for p in on_host
        )
        newmb = caps.get(tier, {}).get("max_mb", 0)
        if used + newmb + int(cfg.get("headroom_mb", 0)) > int(cfg["ram_mb"]):
            raise RuntimeError(
                f"host {host!r} memory admission refused: reserved {used}M + new "
                f"{newmb}M + headroom {cfg.get('headroom_mb', 0)}M > ram {cfg['ram_mb']}M"
            )


def _check_safety_gate() -> None:
    """Check if the agent SDK quota is exhausted and raise an error if so."""
    usage_db = os.environ.get("HERMES_USAGE_DB", "/opt/claude-soma/usage.sqlite")
    if not os.path.exists(usage_db):
        return
    try:
        conn = sqlite3.connect(usage_db)
        conn.row_factory = sqlite3.Row
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT agent_sdk_credits_used, agent_sdk_ceiling FROM daily_snapshots WHERE date = ?",
            (today,)
        ).fetchone()
        conn.close()
        if row:
            used = float(row["agent_sdk_credits_used"])
            ceiling = float(row["agent_sdk_ceiling"])
            if ceiling > 0 and used >= ceiling:
                raise RuntimeError("SUBSCRIPTION EXHAUSTED: Agent SDK quota reached 0%")
    except (sqlite3.Error, ValueError, KeyError):
        # Fail open if DB is inaccessible or malformed
        pass


def spawn_project_impl(
    *, name: str, type_: str, brief: str, permission_mode: str = "acceptEdits",
    host: str = "local", tier: str = "standard",
) -> dict:
    # Reconcile first so ghost leads (dead but still 'active' in the registry)
    # don't wrongly count against the per-host concurrency cap.
    _check_safety_gate()
    active = _reconcile_active()
    _admit_or_raise(host, tier, active)
    tmpl = _resolve_template(type_)
    projects_root = os.environ.get("HERMES_PROJECTS_ROOT", PROJECTS_ROOT)
    cwd = Path(projects_root) / name
    composed_brief = (
        _NOTIFY_CONVENTION + "\n\n"
        + tmpl.get("default_brief", "") + "\n\n"
        + brief
    )

    spawn = spawn_background_lead(
        name=name, brief=composed_brief, cwd=cwd,
        permission_mode=permission_mode or tmpl.get("permission_mode", "acceptEdits"),
        host=host, tier=tier,
    )
    _reg().register(
        name, agent_id=spawn["agent_id"], type_=tmpl["type"],
        cwd=str(cwd), rc_url=spawn.get("rc_url"),
        permission_mode=permission_mode, brief=composed_brief,
        host=host, tier=tier,
    )
    _reg().set_session_uuid(name, spawn["session_uuid"])
    return {
        "agent_id": spawn["agent_id"],
        "rc_url": spawn.get("rc_url", ""),
        "cwd": str(cwd),
        "type": tmpl["type"],
        "session_uuid": spawn["session_uuid"],
        "host": host,
        "tier": tier,
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
            "estimated_next_turn_cost": 1.50,  # Heuristic: $15/MT @ 100k tokens
        }
        for r in rows
    ]


def send_to_project_impl(*, name: str, message: str) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    host = p.get("host", "local")
    if host != "local":
        # Remote lead: deliver via the forced-command guard's `send` verb. The
        # guard checks has-session itself (one ssh hop — no redundant A-side
        # liveness round-trip), base64s nothing here (RemoteRunner does), and
        # rejects control bytes/NUL guard-side.
        from .spawner import RemoteRunner, _raise_on_guard_error
        cp = RemoteRunner(host).send(name, message)
        _raise_on_guard_error(cp, f"send to {name!r} on {host}")
    else:
        if not is_lead_alive(name, host):
            raise RuntimeError(f"lead {name!r} is not running")
        tmux = os.environ.get("HERMES_TMUX_BIN", "/usr/bin/tmux")
        socket = f"{LEAD_SOCKET_PREFIX}{name}"
        session = f"{TMUX_SESSION_PREFIX}{name}"
        try:
            subprocess.run(
                [tmux, "-L", socket, "send-keys", "-t", session, "-l", message],
                check=True, timeout=10, capture_output=True, text=True,
            )
            subprocess.run(
                [tmux, "-L", socket, "send-keys", "-t", session, "Enter"],
                check=True, timeout=10, capture_output=True, text=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            stderr = (getattr(e, "stderr", None) or "")[-500:]
            raise RuntimeError(f"{tmux}: {stderr}") from e
    _reg().touch(name)  # bump idle clock on BOTH local and remote delivery
    return {"name": name, "agent_id": p["agent_id"], "sent_at": time.time(), "delivered": True}


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
    host = p.get("host", "local")
    kill_session(p["agent_id"], host)
    if is_lead_alive(p["agent_id"], host):
        kill_session(p["agent_id"], host)
        if is_lead_alive(p["agent_id"], host):
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


def resume_project_impl(*, name: str, force: bool = False) -> dict:
    """Resume a dead/killed project lead from its cloud session (--resume <uuid>).

    Requires that the project was originally spawned after session_uuid tracking
    was added. If the lead is still alive, raises to prevent a duplicate spawn.
    """
    _check_safety_gate()
    active = _reconcile_active()
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    _admit_or_raise(p.get("host", "local"), p.get("tier", "standard"), active)

    session_uuid = _reg().get_session_uuid(name)
    if session_uuid is None:
        raise RuntimeError(
            f"project {name!r} has no session_uuid; it was spawned before "
            "session tracking was added. Kill it and use spawn_project to "
            "create a new session (resume requires a cloud session ID)."
        )

    if is_lead_alive(name, p.get("host", "local")):
        raise RuntimeError(
            f"project {name!r} is still alive; kill it before resuming."
        )

    cwd = Path(p["cwd"])
    team_members = _reg().get_team_members(name)
    resume_prompt_suffix: str | None = None
    if team_members:
        lines = [
            f"- {m['teammate_handle']} (role: {m['role']}): {m['brief']}"
            for m in team_members
        ]
        resume_prompt_suffix = (
            "Before you were interrupted, your agent team included:\n"
            + "\n".join(lines)
            + "\nYou may want to re-establish your team with the Agent tool."
        )
    spawn = resume_background_lead(
        name=name,
        cwd=cwd,
        permission_mode=p["permission_mode"],
        session_uuid=session_uuid,
        resume_prompt_suffix=resume_prompt_suffix,
        force=force,
        host=p.get("host", "local"),
        tier=p.get("tier", "standard"),
    )
    # Refresh agent_id and rc_url in registry (new tmux session, same uuid).
    # register() upsert does not touch session_uuid, so it is preserved.
    _reg().register(
        name,
        agent_id=spawn["agent_id"],
        type_=p["type"],
        cwd=str(cwd),
        rc_url=spawn.get("rc_url"),
        permission_mode=p["permission_mode"],
        brief=p["brief"],
        host=p.get("host", "local"),
        tier=p.get("tier", "standard"),
    )
    return {
        "agent_id": spawn["agent_id"],
        "rc_url": spawn.get("rc_url", ""),
        "cwd": str(cwd),
        "type": p["type"],
        "session_uuid": session_uuid,
    }


def get_status_impl(name: str) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    # Reconcile: if the registry thinks it's active but the tmux session is
    # gone, the lead vanished -- report (and persist) 'dead' rather than lie.
    host = p.get("host", "local")
    status = p["status"]
    # Local: bool liveness (today's behavior). Remote: tri-state.
    liveness = None
    if status == "active":
        if host == "local":
            liveness = "alive" if is_lead_alive(name, host) else "dead"
        else:
            liveness = lead_liveness(name, host)
    if status == "active" and liveness == "dead":
        _reg().set_status(name, "dead", bump_activity=False)
        status = "dead"
    return {
        "name": p["name"], "agent_id": p["agent_id"], "type": p["type"],
        "cwd": p["cwd"], "rc_url": p["rc_url"], "status": status,
        "host": host, "tier": p.get("tier", "standard"), "liveness": liveness,
        "spawned_at": p["spawned_at"],
        "idle_for_seconds": _reg().idle_for(name),
        "estimated_next_turn_cost": 1.50,  # Heuristic: $15/MT @ 100k tokens
    }


def get_team_impl(name: str) -> dict:
    """Return a lead's live agent-team roster (teammates discovered from its tmux
    panes) and persist the roster to the registry for resume re-establishment.
    Raises if there's no such project.

    Teammates are labelled by their live pane identity (teammate-<pane_index>);
    discover_team does NOT relabel them with registry "canonical" handles -- that
    positional substitution mis-attributed teammates across leads (one lead's
    teammate rendered under another lead in the admin graph, bug 2026-06-16).
    """
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    team = discover_team(p["agent_id"], p.get("host", "local"))
    for member in team:
        _reg().upsert_team_member(
            lead_name=name,
            teammate_handle=member["handle"],
            role=member["role"],
            brief=member["role"],
        )
    return {"name": p["name"], "team": team}


mcp = FastMCP("project_orchestrator")


@mcp.tool()
def spawn_project(
    name: str, type: str, brief: str, permission_mode: str = "acceptEdits",
    host: str = "local", tier: str = "standard",
) -> dict:
    """Spawn a new project-lead background session with its own team scaffold.

    host: 'local' (this VPS, default) or a remote host from
    config/claude/hosts.json (e.g. 'vps-b'). tier: 'standard' (default) or
    'critical' (per-host memory cap on a remote host).
    """
    return spawn_project_impl(
        name=name, type_=type, brief=brief, permission_mode=permission_mode,
        host=host, tier=tier,
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
def resume_project(name: str, force: bool = False) -> dict:
    """Resume a dead or killed project lead from its cloud session.

    Uses --resume <session_uuid> to pull the session from the Claude cloud so
    the lead picks up its full prior transcript even if the local cwd transcript
    is gone. The session_uuid must have been set when the project was originally
    spawned (projects spawned before session tracking require a fresh spawn_project
    instead). The lead must not be currently alive; kill it first if needed.
    """
    return resume_project_impl(name=name, force=force)


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
