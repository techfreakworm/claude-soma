# tests/mcp_servers/test_project_orchestrator.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator import server as orch


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ORCH_DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setenv("HERMES_PROJECTS_ROOT", str(tmp_path / "projects"))
    orch._reset_singletons_for_tests()


def test_spawn_project_registers_and_returns_url() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-1","rc_url":"https://r.example/a-1"}\n')
    with patch("subprocess.run", return_value=fake):
        r = orch.spawn_project_impl(
            name="alpha", type_="web-scraper",
            brief="Build a scraper.", permission_mode="acceptEdits",
        )
    assert r["agent_id"] == "a-1"
    assert r["rc_url"] == "https://r.example/a-1"
    listed = orch.list_projects_impl()
    assert any(p["name"] == "alpha" for p in listed)


def test_kill_project_marks_killed() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-2","rc_url":"https://r/a-2"}\n')
    with patch("subprocess.run", return_value=fake):
        orch.spawn_project_impl(name="beta", type_="custom",
                                brief="Test.", permission_mode="default")
    r = orch.kill_project_impl(name="beta", archive=True)
    assert r["killed_at"] is not None
    assert all(p["name"] != "beta" for p in orch.list_projects_impl())


def test_get_status_returns_idle_for() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-3","rc_url":"https://r/a-3"}\n')
    with patch("subprocess.run", return_value=fake):
        orch.spawn_project_impl(name="gamma", type_="custom",
                                brief="x", permission_mode="default")
    s = orch.get_status_impl("gamma")
    assert s["name"] == "gamma"
    assert "idle_for_seconds" in s


def test_spawn_unknown_type_falls_back_to_custom() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-4","rc_url":"https://r/a-4"}\n')
    with patch("subprocess.run", return_value=fake):
        r = orch.spawn_project_impl(name="d", type_="not-a-type",
                                    brief="x", permission_mode="default")
    assert r["agent_id"] == "a-4"
    p = orch.get_status_impl("d")
    assert p["type"] in {"custom", "not-a-type"}
