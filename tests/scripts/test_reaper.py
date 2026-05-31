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
    monkeypatch.setattr(reaper, "is_lead_alive", lambda name: False)
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


def test_reaper_skips_hibernation_when_lead_alive(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setattr(reaper, "is_lead_alive", lambda name: True)
    r = Registry(db)
    r.register("livep", agent_id="a-3", type_="custom",
               cwd=str(tmp_path / "livep-cwd"), rc_url="https://r/a-3")
    (tmp_path / "livep-cwd").mkdir()
    r._conn.execute(
        "UPDATE projects SET last_activity = ? WHERE name = ?",
        (time.time() - (25 * 3600), "livep"),
    )

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 0
    assert counts["skipped_alive"] == 1
    assert counts["deleted"] == 0
    p = r.get("livep")
    assert p is not None and p["status"] == "active"


def test_reaper_hibernates_when_lead_dead(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setattr(reaper, "is_lead_alive", lambda name: False)
    r = Registry(db)
    r.register("deadp", agent_id="a-4", type_="custom",
               cwd=str(tmp_path / "deadp-cwd"), rc_url="https://r/a-4")
    (tmp_path / "deadp-cwd").mkdir()
    r._conn.execute(
        "UPDATE projects SET last_activity = ? WHERE name = ?",
        (time.time() - (25 * 3600), "deadp"),
    )

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 1
    assert counts["skipped_alive"] == 0
    p = r.get("deadp")
    assert p is not None and p["status"] == "killed"


def test_reaper_hibernates_preserves_session_uuid(tmp_path: Path, monkeypatch) -> None:
    """set_status('killed') must not touch session_uuid — a reaper-hibernated lead
    must remain resumable via resume_project (which reads session_uuid from the row)."""
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setattr(reaper, "is_lead_alive", lambda name: False)
    r = Registry(db)
    r.register("reaped", agent_id="a-5", type_="custom",
               cwd=str(tmp_path / "reaped-cwd"), rc_url="https://r/a-5")
    r.set_session_uuid("reaped", "test-uuid-abc123")
    (tmp_path / "reaped-cwd").mkdir()
    r._conn.execute(
        "UPDATE projects SET last_activity = ? WHERE name = ?",
        (time.time() - (25 * 3600), "reaped"),
    )

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 1
    p = r.get("reaped")
    assert p is not None and p["status"] == "killed"
    # session_uuid must survive the set_status call so resume_project can use it.
    assert r.get_session_uuid("reaped") == "test-uuid-abc123"
