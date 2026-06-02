from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from claude_soma.mcp_servers.hermes_api.notify_store import EventStore
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


def test_reaper_kills_on_turn_cap(tmp_path: Path, monkeypatch) -> None:
    """Leads with derived_turns >= HERMES_LEAD_TURN_CAP are killed even when alive."""
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HERMES_LEAD_TURN_CAP", "50")
    monkeypatch.setattr(reaper, "is_lead_alive", lambda _name: True)
    killed: list[str] = []
    monkeypatch.setattr(reaper, "kill_session", lambda name: killed.append(name))

    r = Registry(db)
    r.register("cap-lead", agent_id="a-1", type_="custom",
               cwd=str(tmp_path / "cap-cwd"), rc_url="https://r/a-1")
    (tmp_path / "cap-cwd").mkdir()

    store = EventStore(db)
    for _ in range(51):
        store.insert_event(
            lead="cap-lead",
            type_="STARTED",
            ts=time.time(),
            payload_json='{"task": "x"}',
        )
    store.close()

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)

    assert counts["cap_killed"] == 1
    assert "cap-lead" in killed
    p = r.get("cap-lead")
    assert p is not None and p["status"] == "killed"

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT * FROM lead_events WHERE lead = ? AND type = 'NEEDS_INPUT'",
        ("cap-lead",),
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_reaper_kills_on_context_cap(tmp_path: Path, monkeypatch) -> None:
    """Leads whose estimated token count >= HERMES_LEAD_CONTEXT_CAP_TOKENS are killed."""
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HERMES_LEAD_CONTEXT_CAP_TOKENS", "200000")
    monkeypatch.setattr(reaper, "is_lead_alive", lambda _name: True)
    killed: list[str] = []
    monkeypatch.setattr(reaper, "kill_session", lambda name: killed.append(name))

    r = Registry(db)
    r.register("ctx-lead", agent_id="a-1", type_="custom",
               cwd=str(tmp_path / "ctx-cwd"), rc_url="https://r/a-1")
    (tmp_path / "ctx-cwd").mkdir()

    store = EventStore(db)
    # Need total payload chars > 200000 * 4 = 800000 to push est_tokens over cap.
    big_payload = '{"data": "' + ("x" * 800001) + '"}'
    store.insert_event(
        lead="ctx-lead",
        type_="STARTED",
        ts=time.time(),
        payload_json=big_payload,
    )
    store.close()

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)

    assert counts["cap_killed"] == 1
    assert "ctx-lead" in killed
    p = r.get("ctx-lead")
    assert p is not None and p["status"] == "killed"

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT * FROM lead_events WHERE lead = ? AND type = 'NEEDS_INPUT'",
        ("ctx-lead",),
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_reaper_does_not_kill_below_caps(tmp_path: Path, monkeypatch) -> None:
    """Leads with turns and tokens well below the caps are left running."""
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_LEAD_TURN_CAP", "50")
    monkeypatch.setenv("HERMES_LEAD_CONTEXT_CAP_TOKENS", "200000")
    monkeypatch.setattr(reaper, "is_lead_alive", lambda _name: True)
    killed: list[str] = []
    monkeypatch.setattr(reaper, "kill_session", lambda name: killed.append(name))

    r = Registry(db)
    r.register("healthy-lead", agent_id="a-1", type_="custom",
               cwd=str(tmp_path / "healthy-cwd"), rc_url="https://r/a-1")

    store = EventStore(db)
    for _ in range(5):
        store.insert_event(
            lead="healthy-lead",
            type_="STARTED",
            ts=time.time(),
            payload_json='{"task": "x"}',
        )
    store.close()

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)

    assert counts["cap_killed"] == 0
    assert "healthy-lead" not in killed
    assert r.get("healthy-lead")["status"] == "active"


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
