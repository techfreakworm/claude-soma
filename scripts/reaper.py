#!/usr/bin/env python3
"""Hibernate idle project-leads and delete long-dead ones.

Invoked every 6h by systemd timer.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

from claude_soma.mcp_servers.hermes_api.notify_store import EventStore
from claude_soma.mcp_servers.project_orchestrator.registry import Registry
from claude_soma.mcp_servers.project_orchestrator.spawner import (
    _estimate_context_tokens,
    is_lead_alive,
    kill_session,
)

logger = logging.getLogger(__name__)


DB = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
ARCHIVE_ROOT = Path(os.environ.get(
    "HERMES_ARCHIVE_ROOT", "/opt/claude-soma/archive"
))


def _count_started_events(db_path: str, lead: str) -> int:
    """Return the number of STARTED events for a lead; 0 on any error."""
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT COUNT(*) FROM lead_events WHERE lead = ? AND type = 'STARTED'",
            (lead,),
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def _archive_project_memory(name: str, cwd: str) -> None:
    src = Path(cwd) / ".claude"
    if not src.exists():
        return
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    dst = ARCHIVE_ROOT / f"{name}-{int(time.time())}"
    try:
        shutil.copytree(src, dst, dirs_exist_ok=False)
    except (FileExistsError, OSError):
        pass


def run_once(
    idle_hibernate_seconds: float = 24 * 3600,
    idle_delete_seconds: float = 7 * 24 * 3600,
) -> dict:
    db_path = os.environ.get("HERMES_ORCH_DB", DB)
    archive_root = Path(os.environ.get("HERMES_ARCHIVE_ROOT", str(ARCHIVE_ROOT)))
    turn_cap = int(os.environ.get("HERMES_LEAD_TURN_CAP", "50"))
    ctx_cap = int(os.environ.get("HERMES_LEAD_CONTEXT_CAP_TOKENS", "200000"))
    reg = Registry(db_path)
    now = time.time()
    hibernated = 0
    skipped_alive = 0
    deleted = 0
    cap_killed = 0
    _store: EventStore | None = None
    try:
        for p in reg.list_all():
            if p["status"] != "active":
                if now - float(p["last_activity"]) > idle_delete_seconds:
                    reg.delete(p["name"])
                    deleted += 1
                continue
            idle = now - float(p["last_activity"])
            alive = is_lead_alive(p["agent_id"])
            if idle > idle_hibernate_seconds:
                # Conservative: skip idle-hibernation if the tmux session is still
                # alive. last_activity is only bumped by send_to_project_impl (MCP
                # path); the bot also talks to leads via raw `tmux send-keys`, which
                # doesn't touch the registry, so a chatty lead can look stale here
                # while still doing work. Hibernating a live lead diverges registry
                # from reality and hides it from the admin panel — 2026-05-29.
                if not alive:
                    _archive_to(archive_root, p["name"], p["cwd"])
                    uuid = reg.get_session_uuid(p["name"])
                    reg.set_status(p["name"], "killed")
                    logger.info(
                        "hibernated lead %r after %.1fh idle; session_uuid=%r (resumable)",
                        p["name"], idle / 3600, uuid,
                    )
                    hibernated += 1
                    continue
                skipped_alive += 1
                # Fall through to cap check — a lead that is still alive but has
                # been running for over 24h may be stuck; the caps can catch it.
            if alive:
                derived_turns = _count_started_events(db_path, p["name"])
                est_tokens = _estimate_context_tokens(p["name"])
                if derived_turns >= turn_cap or est_tokens >= ctx_cap:
                    kill_session(p["name"])
                    _archive_to(archive_root, p["name"], p["cwd"])
                    reg.set_status(p["name"], "killed")
                    if _store is None:
                        _store = EventStore(db_path)
                    _store.insert_event(
                        lead=p["name"],
                        type_="NEEDS_INPUT",
                        ts=time.time(),
                        payload_json=json.dumps({
                            "question": (
                                f"Lead {p['name']} auto-killed at "
                                f"turns={derived_turns} est_tokens={est_tokens}; "
                                "respawn or investigate?"
                            )
                        }),
                    )
                    logger.info(
                        "auto-killed lead=%r turns=%d ctx=%d",
                        p["name"], derived_turns, est_tokens,
                    )
                    cap_killed += 1
    finally:
        reg.close()
        if _store is not None:
            _store.close()
    return {
        "hibernated": hibernated,
        "skipped_alive": skipped_alive,
        "deleted": deleted,
        "cap_killed": cap_killed,
    }


def _archive_to(archive_root: Path, name: str, cwd: str) -> None:
    src = Path(cwd) / ".claude"
    if not src.exists():
        return
    archive_root.mkdir(parents=True, exist_ok=True)
    dst = archive_root / f"{name}-{int(time.time())}"
    try:
        shutil.copytree(src, dst, dirs_exist_ok=False)
    except (FileExistsError, OSError):
        pass


def main() -> None:
    counts = run_once()
    print(
        f"reaper: hibernated={counts['hibernated']} skipped_alive={counts['skipped_alive']} "
        f"deleted={counts['deleted']} cap_killed={counts['cap_killed']}"
    )


if __name__ == "__main__":
    main()
