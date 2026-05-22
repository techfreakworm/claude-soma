# tests/mcp_servers/test_project_orchestrator_registry.py
from __future__ import annotations

import time
from pathlib import Path

import pytest

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
