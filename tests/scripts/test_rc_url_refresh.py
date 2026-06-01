from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch
import subprocess as real_subprocess

from claude_soma.mcp_servers.project_orchestrator.registry import Registry

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import rc_url_refresh  # type: ignore  # noqa: E402

OLD_URL = "https://claude.ai/code/session_OLDoldold00000000000001"
NEW_URL = "https://claude.ai/code/session_NEWnewnew00000000000001"
SAME_URL = "https://claude.ai/code/session_SAMEsamesame000000000001"


def _make_result(stdout: str = "") -> MagicMock:
    r = MagicMock(spec=real_subprocess.CompletedProcess)
    r.stdout = stdout
    r.returncode = 0
    return r


def _idle_pane() -> str:
    return "Working on task...\n❯ \n"


def _busy_pane() -> str:
    return "Bloviating…\n"


def _pane_with_url(url: str) -> str:
    return f"Here is your remote control URL:\n{url}\n❯ \n"


def _setup_registry(tmp_path: Path, leads: list[dict]) -> Registry:
    db = tmp_path / "reg.sqlite"
    reg = Registry(str(db))
    for lead in leads:
        reg.register(
            lead["name"],
            agent_id=lead["agent_id"],
            type_="custom",
            cwd=str(tmp_path / lead["name"]),
            rc_url=lead["rc_url"],
        )
    return reg


def test_refresh_url_changes_updates_registry(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    log = tmp_path / "rc-refresh.log"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_RC_REFRESH_LOG", str(log))
    monkeypatch.setenv("HERMES_RC_REFRESH_SLEEP", "0")

    reg = _setup_registry(tmp_path, [
        {"name": "proj-a", "agent_id": "soma-proj-proj-a", "rc_url": OLD_URL},
    ])
    monkeypatch.setattr(rc_url_refresh, "is_lead_alive", lambda _: True)
    monkeypatch.setattr(rc_url_refresh.time, "sleep", lambda _: None)

    side_effects = [
        _make_result(_idle_pane()),          # busy-check capture-pane
        _make_result(""),                    # send-keys -l /remote-control
        _make_result(""),                    # send-keys Enter
        _make_result(_pane_with_url(NEW_URL)),  # URL capture-pane
        _make_result(""),                    # send-keys Enter (dismiss)
    ]
    with patch.object(rc_url_refresh.subprocess, "run", side_effect=side_effects):
        counts = rc_url_refresh.run_once()

    assert counts["refreshed"] == 1
    assert counts["noop"] == 0
    assert counts["errors"] == 0

    p = reg.get("proj-a")
    assert p is not None
    assert p["rc_url"] == NEW_URL

    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    lead_line = next(ln for ln in lines if ln.get("lead") == "proj-a")
    assert lead_line["result"] == "refreshed"
    summary = lines[-1]["summary"]
    assert summary["refreshed"] == 1

    reg.close()


def test_refresh_url_unchanged_is_noop(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    log = tmp_path / "rc-refresh.log"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_RC_REFRESH_LOG", str(log))
    monkeypatch.setenv("HERMES_RC_REFRESH_SLEEP", "0")

    old_ts = time.time() - 3600
    reg = _setup_registry(tmp_path, [
        {"name": "proj-b", "agent_id": "soma-proj-proj-b", "rc_url": SAME_URL},
    ])
    with reg._lock:
        reg._conn.execute(
            "UPDATE projects SET last_activity = ? WHERE name = ?",
            (old_ts, "proj-b"),
        )
    monkeypatch.setattr(rc_url_refresh, "is_lead_alive", lambda _: True)
    monkeypatch.setattr(rc_url_refresh.time, "sleep", lambda _: None)

    side_effects = [
        _make_result(_idle_pane()),
        _make_result(""),
        _make_result(""),
        _make_result(_pane_with_url(SAME_URL)),
        _make_result(""),
    ]
    with patch.object(rc_url_refresh.subprocess, "run", side_effect=side_effects):
        counts = rc_url_refresh.run_once()

    assert counts["noop"] == 1
    assert counts["refreshed"] == 0

    p = reg.get("proj-b")
    assert p is not None
    assert p["rc_url"] == SAME_URL
    assert abs(p["last_activity"] - old_ts) < 1.0, "last_activity must not change on noop"

    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    lead_line = next(ln for ln in lines if ln.get("lead") == "proj-b")
    assert lead_line["result"] == "noop"

    reg.close()


def test_refresh_skips_dead_lead(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    log = tmp_path / "rc-refresh.log"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_RC_REFRESH_LOG", str(log))

    _setup_registry(tmp_path, [
        {"name": "proj-c", "agent_id": "soma-proj-proj-c", "rc_url": OLD_URL},
    ])
    monkeypatch.setattr(rc_url_refresh, "is_lead_alive", lambda _: False)

    with patch.object(rc_url_refresh.subprocess, "run") as mock_run:
        counts = rc_url_refresh.run_once()
        mock_run.assert_not_called()

    assert counts["skipped_dead"] == 1
    assert counts["refreshed"] == 0

    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    lead_line = next(ln for ln in lines if ln.get("lead") == "proj-c")
    assert lead_line["result"] == "skipped:dead"


def test_refresh_skips_busy_lead(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    log = tmp_path / "rc-refresh.log"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_RC_REFRESH_LOG", str(log))

    _setup_registry(tmp_path, [
        {"name": "proj-d", "agent_id": "soma-proj-proj-d", "rc_url": OLD_URL},
    ])
    monkeypatch.setattr(rc_url_refresh, "is_lead_alive", lambda _: True)

    side_effects = [
        _make_result(_busy_pane()),  # busy-check capture-pane
    ]
    with patch.object(rc_url_refresh.subprocess, "run", side_effect=side_effects) as mock_run:
        counts = rc_url_refresh.run_once()

    assert counts["skipped_busy"] == 1
    assert counts["refreshed"] == 0
    assert mock_run.call_count == 1, "only the busy-check capture-pane should be called"

    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    lead_line = next(ln for ln in lines if ln.get("lead") == "proj-d")
    assert lead_line["result"] == "skipped:busy"


def test_refresh_handles_unparseable_pane(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    log = tmp_path / "rc-refresh.log"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_RC_REFRESH_LOG", str(log))
    monkeypatch.setenv("HERMES_RC_REFRESH_SLEEP", "0")

    _setup_registry(tmp_path, [
        {"name": "proj-e", "agent_id": "soma-proj-proj-e", "rc_url": OLD_URL},
    ])
    monkeypatch.setattr(rc_url_refresh, "is_lead_alive", lambda _: True)
    monkeypatch.setattr(rc_url_refresh.time, "sleep", lambda _: None)

    side_effects = [
        _make_result(_idle_pane()),     # busy-check
        _make_result(""),               # send-keys -l
        _make_result(""),               # send-keys Enter
        _make_result("no url here\n"),  # URL capture-pane: no match
        _make_result(""),               # send-keys Enter dismiss
    ]
    with patch.object(rc_url_refresh.subprocess, "run", side_effect=side_effects):
        counts = rc_url_refresh.run_once()

    assert counts["errors"] == 1
    assert counts["refreshed"] == 0

    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    lead_line = next(ln for ln in lines if ln.get("lead") == "proj-e")
    assert lead_line["result"] == "error"
    assert "detail" in lead_line


def test_is_busy_with_idle_prompt() -> None:
    pane = "Working on task...\nSome output\n❯ \n"
    assert rc_url_refresh._is_busy(pane) is False


def test_is_busy_with_spinner() -> None:
    pane = "Doing stuff\nBloviating…\n"
    assert rc_url_refresh._is_busy(pane) is True


def test_is_busy_ambiguous() -> None:
    pane = "Running task...\nSome unrecognized output here\n"
    assert rc_url_refresh._is_busy(pane) is True


def test_refresh_summary_line_emitted(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    log = tmp_path / "rc-refresh.log"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_RC_REFRESH_LOG", str(log))
    monkeypatch.setenv("HERMES_RC_REFRESH_SLEEP", "0")

    reg = _setup_registry(tmp_path, [
        {"name": "live-changed",  "agent_id": "soma-proj-live-changed",  "rc_url": OLD_URL},
        {"name": "live-same",     "agent_id": "soma-proj-live-same",     "rc_url": SAME_URL},
        {"name": "dead-lead",     "agent_id": "soma-proj-dead-lead",     "rc_url": OLD_URL},
        {"name": "busy-lead",     "agent_id": "soma-proj-busy-lead",     "rc_url": OLD_URL},
    ])

    def fake_alive(agent_id: str) -> bool:
        return "dead" not in agent_id

    monkeypatch.setattr(rc_url_refresh, "is_lead_alive", fake_alive)
    monkeypatch.setattr(rc_url_refresh.time, "sleep", lambda _: None)

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "capture-pane" in cmd_str:
            if "busy-lead" in cmd_str:
                return _make_result(_busy_pane())
            elif "live-changed" in cmd_str:
                return _make_result(_pane_with_url(NEW_URL))
            elif "live-same" in cmd_str:
                return _make_result(_pane_with_url(SAME_URL))
            else:
                return _make_result(_idle_pane())
        return _make_result("")

    with patch.object(rc_url_refresh.subprocess, "run", side_effect=fake_run):
        counts = rc_url_refresh.run_once()

    assert counts["refreshed"] == 1
    assert counts["noop"] == 1
    assert counts["skipped_dead"] == 1
    assert counts["skipped_busy"] == 1
    assert counts["errors"] == 0

    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    last = lines[-1]
    assert "summary" in last
    s = last["summary"]
    assert s["refreshed"] == 1
    assert s["noop"] == 1
    assert s["skipped_dead"] == 1
    assert s["skipped_busy"] == 1
    assert s["errors"] == 0

    reg.close()
