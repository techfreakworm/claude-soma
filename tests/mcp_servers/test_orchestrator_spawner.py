# tests/mcp_servers/test_orchestrator_spawner.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_soma.mcp_servers.project_orchestrator.spawner import (
    spawn_background_lead, BriefTooLong
)


def test_spawn_calls_claude_bg_with_expected_args(tmp_path: Path) -> None:
    cwd = tmp_path / "my-project"
    cwd.mkdir()
    fake = MagicMock()
    fake.stdout = '{"agent_id":"a-abc","rc_url":"https://claude.ai/x"}\n'
    fake.returncode = 0
    with patch("subprocess.run", return_value=fake) as run:
        result = spawn_background_lead(
            name="my-project", brief="Build it.", cwd=cwd,
            permission_mode="acceptEdits",
        )
    args = run.call_args[0][0]
    assert "claude" in args[0] or args[0] == "claude"
    assert "--bg" in args
    assert "--name" in args and "my-project" in args
    assert "--add-dir" in args and str(cwd) in args
    assert "--permission-mode" in args and "acceptEdits" in args
    assert result["agent_id"] == "a-abc"
    assert result["rc_url"].startswith("https://")


def test_spawn_rejects_long_brief(tmp_path: Path) -> None:
    with pytest.raises(BriefTooLong):
        spawn_background_lead(
            name="big", brief="x" * 200_000, cwd=tmp_path,
            permission_mode="acceptEdits",
        )
