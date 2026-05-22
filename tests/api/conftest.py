from __future__ import annotations

from pathlib import Path

import pytest

from claude_soma.mcp_servers.project_orchestrator import server as orch


@pytest.fixture(autouse=True)
def _isolate_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_ORCH_DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setenv("HERMES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("HERMES_CLAUDE_BIN", "/usr/bin/false")
    monkeypatch.setenv("HERMES_USAGE_DB", str(tmp_path / "usage.sqlite"))
    monkeypatch.setenv("HERMES_BROADCAST_QUEUE", str(tmp_path / "broadcast.jsonl"))
    orch._reset_singletons_for_tests()
