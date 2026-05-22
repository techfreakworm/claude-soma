from __future__ import annotations

from pathlib import Path

import pytest

from claude_soma.mcp_servers.hermes_api.claude_state import (
    list_sessions, read_activity_log, read_memory
)


def test_read_activity_log_returns_recent_lines(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "activity.jsonl"
    log.write_text(
        '{"ts":"2026-05-22T10:00:00Z","tool":"Read","session":"s-1"}\n'
        '{"ts":"2026-05-22T10:00:01Z","tool":"Edit","session":"s-1"}\n'
    )
    monkeypatch.setenv("HERMES_ACTIVITY_LOG", str(log))
    rows = read_activity_log(limit=10)
    assert len(rows) == 2
    assert rows[0]["tool"] == "Read"


def test_read_memory_returns_text(tmp_path: Path, monkeypatch) -> None:
    proj_dir = tmp_path / "encoded-proj"
    (proj_dir / "memory").mkdir(parents=True)
    (proj_dir / "memory" / "MEMORY.md").write_text("- thing\n- other\n")
    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(tmp_path))
    text = read_memory("encoded-proj")
    assert "thing" in text


def test_list_sessions_returns_empty_when_no_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CLAUDE_JOBS_ROOT", str(tmp_path))
    assert list_sessions() == []
