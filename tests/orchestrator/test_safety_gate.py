
import pytest
import sqlite3
import os
from unittest.mock import patch, MagicMock
from claude_soma.mcp_servers.project_orchestrator.server import spawn_project_impl, list_projects_impl, _reg, _reset_singletons_for_tests

@pytest.fixture(autouse=True)
def clean_registry():
    _reset_singletons_for_tests()
    yield

def test_spawn_project_fails_when_quota_exhausted(tmp_path):
    # Setup a mock usage DB with 0% remaining
    usage_db = tmp_path / "usage.sqlite"
    conn = sqlite3.connect(usage_db)
    conn.execute("""
    CREATE TABLE daily_snapshots(
        date TEXT PRIMARY KEY,
        interactive_credits_used REAL DEFAULT 0,
        interactive_ceiling REAL DEFAULT 0,
        agent_sdk_credits_used REAL DEFAULT 0,
        agent_sdk_ceiling REAL DEFAULT 0,
        recorded_at REAL DEFAULT 0
    );
    """)
    from datetime import date
    today = date.today().isoformat()
    # 100/100 used = 0% remaining
    conn.execute(
        "INSERT INTO daily_snapshots (date, agent_sdk_credits_used, agent_sdk_ceiling) VALUES (?, ?, ?)",
        (today, 100.0, 100.0)
    )
    conn.commit()
    conn.close()

    with patch.dict(os.environ, {
        "HERMES_USAGE_DB": str(usage_db),
        "HERMES_PROJECTS_ROOT": str(tmp_path),
        "HERMES_ORCH_DB": str(tmp_path / "registry.sqlite")
    }):
        with pytest.raises(RuntimeError, match="SUBSCRIPTION EXHAUSTED"):
            spawn_project_impl(name="test-proj", type_="custom", brief="test")

def test_list_projects_includes_cost_heuristic(tmp_path):
    # Register a mock project
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    
    with patch.dict(os.environ, {
        "HERMES_PROJECTS_ROOT": str(projects_root),
        "HERMES_ORCH_DB": str(tmp_path / "registry.sqlite")
    }):
        # We need to mock is_lead_alive to return True for our project
        with patch("claude_soma.mcp_servers.project_orchestrator.server.is_lead_alive", return_value=True):
             # Mock the registry to have one active project
            _reg().register("test-proj", agent_id="soma-proj-test", type_="custom", cwd=str(projects_root/"test-proj"), rc_url="http://rc")
            
            projects = list_projects_impl()
            assert len(projects) == 1
            assert "estimated_next_turn_cost" in projects[0]
            assert projects[0]["estimated_next_turn_cost"] == 1.50

def test_get_status_includes_cost_heuristic(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    
    with patch.dict(os.environ, {
        "HERMES_PROJECTS_ROOT": str(projects_root),
        "HERMES_ORCH_DB": str(tmp_path / "registry.sqlite")
    }):
        from claude_soma.mcp_servers.project_orchestrator.server import get_status_impl
        with patch("claude_soma.mcp_servers.project_orchestrator.server.is_lead_alive", return_value=True):
            _reg().register("test-proj", agent_id="soma-proj-test", type_="custom", cwd=str(projects_root/"test-proj"), rc_url="http://rc")
            
            status = get_status_impl("test-proj")
            assert "estimated_next_turn_cost" in status
            assert status["estimated_next_turn_cost"] == 1.50
