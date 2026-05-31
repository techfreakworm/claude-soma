# tests/mcp_servers/test_project_orchestrator_registry.py
from __future__ import annotations

import time
from pathlib import Path

from claude_soma.mcp_servers.project_orchestrator.registry import Registry


def test_register_then_get(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("foo", agent_id="a-1", type_="web-scraper",
               cwd="/home/ubuntu/projects/foo", rc_url="https://x")
    p = r.get("foo")
    assert p is not None
    assert p["name"] == "foo"
    assert p["agent_id"] == "a-1"
    assert p["type"] == "web-scraper"
    assert p["status"] == "active"


def test_list_active_excludes_killed(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("a", agent_id="a-1", type_="llm-app", cwd="/x", rc_url="https://a")
    r.register("b", agent_id="a-2", type_="llm-app", cwd="/y", rc_url="https://b")
    r.set_status("a", "killed")
    actives = r.list_active()
    assert {p["name"] for p in actives} == {"b"}


def test_touch_updates_last_activity(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("x", agent_id="a-1", type_="custom", cwd="/x", rc_url="https://x")
    before = r.get("x")["last_activity"]
    time.sleep(0.05)
    r.touch("x")
    after = r.get("x")["last_activity"]
    assert after > before


def test_idle_for_seconds_increases(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("x", agent_id="a-1", type_="custom", cwd="/x", rc_url="https://x")
    time.sleep(0.5)
    assert r.idle_for("x") >= 0.4


def test_set_status_default_bumps_last_activity(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("x", agent_id="a-1", type_="custom", cwd="/x", rc_url="https://x")
    before = r.get("x")["last_activity"]
    time.sleep(0.05)
    r.set_status("x", "killed")
    assert r.get("x")["status"] == "killed"
    assert r.get("x")["last_activity"] > before


def test_set_status_no_bump_preserves_last_activity(tmp_path: Path) -> None:
    """Liveness reconciliation flips a vanished lead to 'dead' with
    bump_activity=False so a long-dead lead doesn't look freshly active."""
    r = Registry(tmp_path / "reg.sqlite")
    r.register("x", agent_id="a-1", type_="custom", cwd="/x", rc_url="https://x")
    before = r.get("x")["last_activity"]
    time.sleep(0.05)
    r.set_status("x", "dead", bump_activity=False)
    assert r.get("x")["status"] == "dead"
    assert r.get("x")["last_activity"] == before


def test_register_routine_stores_metadata_unit(tmp_path: Path) -> None:
    """register_routine with metadata={'unit': ...} must round-trip the unit name."""
    r = Registry(tmp_path / "reg.sqlite")
    r.register_routine(
        "healthcheck",
        kind="local",
        schedule="every 10 min",
        created_by="system",
        metadata={"unit": "claude-soma-healthcheck.timer"},
    )
    row = r.get_routine("healthcheck")
    assert row is not None
    assert row["metadata"] == {"unit": "claude-soma-healthcheck.timer"}


def test_registry_usable_from_other_threads(tmp_path: Path) -> None:
    """Regression: the connection is opened on THIS thread but FastAPI runs sync
    route handlers in a threadpool, so .get()/.list_*()/.set_status() get called
    from other threads. Before check_same_thread=False + the lock this raised
    `sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread` (it 500'd /api/projects/{name}/team)."""
    from concurrent.futures import ThreadPoolExecutor

    r = Registry(tmp_path / "reg.sqlite")  # opened on the main/test thread
    r.register("p", agent_id="soma-proj-p", type_="custom", cwd="/x", rc_url="https://x")

    def worker(_: int) -> str:
        # Runs on a DIFFERENT thread than the one that opened the connection.
        assert r.get("p")["name"] == "p"
        assert any(row["name"] == "p" for row in r.list_active())
        r.touch("p")
        r.set_status("p", "active")
        return r.get("p")["status"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in [pool.submit(worker, i) for i in range(16)]]
    assert results == ["active"] * 16  # no ProgrammingError raised in any worker
    r.close()


# --- session_uuid tests ---

def test_get_session_uuid_returns_none_for_new_project(tmp_path: Path) -> None:
    """Projects registered without a session_uuid have None by default."""
    r = Registry(tmp_path / "reg.sqlite")
    r.register("nosid", agent_id="a-1", type_="custom", cwd="/x", rc_url=None)
    assert r.get_session_uuid("nosid") is None


def test_get_session_uuid_returns_none_for_unknown_project(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    assert r.get_session_uuid("does-not-exist") is None


def test_set_and_get_session_uuid(tmp_path: Path) -> None:
    """set_session_uuid persists the uuid; get_session_uuid retrieves it.
    The full get() dict also exposes the column."""
    r = Registry(tmp_path / "reg.sqlite")
    r.register("withsid", agent_id="a-1", type_="custom", cwd="/x", rc_url=None)
    test_uuid = "aaaabbbb-cccc-4ddd-eeee-ffffffffffff"
    r.set_session_uuid("withsid", test_uuid)
    assert r.get_session_uuid("withsid") == test_uuid
    assert r.get("withsid")["session_uuid"] == test_uuid


def test_migration_adds_column_to_existing_db(tmp_path: Path) -> None:
    """Simulate an existing DB that pre-dates the session_uuid column.
    Opening a Registry on it must add the column (migration) and leave
    existing rows intact with session_uuid=NULL."""
    import sqlite3

    db_path = tmp_path / "old.sqlite"
    # Create a minimal DB without session_uuid (pre-migration schema)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE projects (
            name TEXT PRIMARY KEY, agent_id TEXT NOT NULL, type TEXT NOT NULL,
            cwd TEXT NOT NULL, rc_url TEXT, status TEXT NOT NULL DEFAULT 'active',
            permission_mode TEXT NOT NULL DEFAULT 'acceptEdits',
            spawned_at REAL NOT NULL, last_activity REAL NOT NULL, brief TEXT
        )
    """)
    conn.execute(
        "INSERT INTO projects VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("old-lead", "soma-proj-old-lead", "custom", "/x", None, "active",
         "acceptEdits", 1.0, 1.0, None),
    )
    conn.commit()
    conn.close()

    # Opening Registry must run the migration without crashing.
    r = Registry(db_path)
    row = r.get("old-lead")
    assert row is not None
    assert row["name"] == "old-lead"
    # Existing row gets session_uuid=NULL from the ALTER TABLE default.
    assert row["session_uuid"] is None
    # New rows can have a session_uuid.
    r.register("new-lead", agent_id="a-2", type_="custom", cwd="/y", rc_url=None)
    r.set_session_uuid("new-lead", "new-uuid-1234")
    assert r.get_session_uuid("new-lead") == "new-uuid-1234"


def test_register_upsert_preserves_session_uuid(tmp_path: Path) -> None:
    """register() upsert does NOT clobber an existing session_uuid.
    A re-register (e.g. agent_id refresh) must leave the uuid intact."""
    r = Registry(tmp_path / "reg.sqlite")
    r.register("rep", agent_id="a-1", type_="custom", cwd="/x", rc_url=None)
    r.set_session_uuid("rep", "stable-uuid-0000")
    # Re-register with a new agent_id (upsert path).
    r.register("rep", agent_id="a-2", type_="custom", cwd="/x", rc_url=None)
    # session_uuid must survive the upsert.
    assert r.get_session_uuid("rep") == "stable-uuid-0000"
