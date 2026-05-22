from __future__ import annotations

import sys
import time
from pathlib import Path

from claude_soma.mcp_servers.project_orchestrator.registry import Registry

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import reaper  # type: ignore  # noqa: E402


def test_reaper_hibernates_after_threshold(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    r = Registry(db)
    r.register("oldp", agent_id="a-1", type_="custom",
               cwd=str(tmp_path / "oldp-cwd"), rc_url="https://r/a-1")
    (tmp_path / "oldp-cwd").mkdir()
    r._conn.execute(
        "UPDATE projects SET last_activity = ? WHERE name = ?",
        (time.time() - (25 * 3600), "oldp"),
    )

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 1
    p = r.get("oldp")
    assert p is not None and p["status"] == "killed"


def test_reaper_keeps_recent_project(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    r = Registry(db)
    r.register("freshp", agent_id="a-2", type_="custom",
               cwd=str(tmp_path / "fresh-cwd"), rc_url="https://r/a-2")
    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 0
    assert r.get("freshp")["status"] == "active"
