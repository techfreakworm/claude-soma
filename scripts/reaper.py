#!/usr/bin/env python3
"""Hibernate idle project-leads and delete long-dead ones.

Invoked every 6h by systemd timer.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from claude_soma.mcp_servers.project_orchestrator.registry import Registry


DB = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
ARCHIVE_ROOT = Path(os.environ.get(
    "HERMES_ARCHIVE_ROOT", "/opt/claude-soma/archive"
))


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
    reg = Registry(db_path)
    now = time.time()
    hibernated = 0
    deleted = 0
    try:
        for p in reg.list_all():
            if p["status"] != "active":
                if now - float(p["last_activity"]) > idle_delete_seconds:
                    reg.delete(p["name"])
                    deleted += 1
                continue
            idle = now - float(p["last_activity"])
            if idle > idle_hibernate_seconds:
                _archive_to(archive_root, p["name"], p["cwd"])
                reg.set_status(p["name"], "killed")
                hibernated += 1
    finally:
        reg.close()
    return {"hibernated": hibernated, "deleted": deleted}


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
    print(f"reaper: hibernated={counts['hibernated']} deleted={counts['deleted']}")


if __name__ == "__main__":
    main()
