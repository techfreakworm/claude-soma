from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _projects_root() -> Path:
    return Path(os.environ.get(
        "HERMES_CLAUDE_PROJECTS_ROOT",
        str(Path.home() / ".claude" / "projects"),
    ))


def _jobs_root() -> Path:
    return Path(os.environ.get(
        "HERMES_CLAUDE_JOBS_ROOT",
        str(Path.home() / ".claude" / "jobs"),
    ))


def _activity_log() -> Path:
    return Path(os.environ.get(
        "HERMES_ACTIVITY_LOG",
        str(Path.home() / ".claude-soma" / "activity.jsonl"),
    ))


def list_sessions() -> list[dict[str, Any]]:
    root = _jobs_root()
    if not root.exists():
        return []
    out = []
    for job_dir in sorted(root.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        state_file = job_dir / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
            except json.JSONDecodeError:
                continue
            out.append({
                "id": job_dir.name,
                "name": state.get("name", job_dir.name),
                "status": state.get("status", "unknown"),
                "started_at": state.get("started_at"),
            })
    return out


def read_activity_log(limit: int = 200) -> list[dict[str, Any]]:
    log = _activity_log()
    if not log.exists():
        return []
    lines = log.read_text().splitlines()[-limit:]
    out: list[dict[str, Any]] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def read_memory(project_slug: str) -> str:
    root = _projects_root().resolve()
    path = (root / project_slug / "memory" / "MEMORY.md").resolve()
    if not path.is_relative_to(root):
        return ""
    if not path.exists():
        return ""
    return path.read_text()


def list_transcript_threads(limit: int = 50) -> list[dict[str, Any]]:
    """List most-recently-modified transcript files across projects."""
    root = _projects_root()
    if not root.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for proj_dir in root.iterdir():
        tdir = proj_dir / "transcripts"
        if tdir.exists():
            for f in tdir.glob("*.jsonl"):
                candidates.append((f.stat().st_mtime, f))
    candidates.sort(reverse=True)
    return [
        {
            "thread_id": f.stem,
            "project": f.parent.parent.name,
            "modified_at": mtime,
            "size_bytes": f.stat().st_size,
        }
        for mtime, f in candidates[:limit]
    ]


def read_transcript(thread_id: str, project: str) -> list[dict[str, Any]]:
    root = _projects_root().resolve()
    path = (root / project / "transcripts" / f"{thread_id}.jsonl").resolve()
    if not path.is_relative_to(root):
        return []
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
