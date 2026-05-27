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
