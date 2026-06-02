from __future__ import annotations

import asyncio
import datetime
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from claude_soma.mcp_servers.hermes_api.claude_state import (
    list_sessions, read_activity_log, read_memory
)
from claude_soma.mcp_servers.hermes_api.server import (
    _parse_when,
    schedule_reminder,
    _prewarm_routines_cache,
)
from claude_soma.mcp_servers.hermes_api.socket import _serve, call


def _short_sock_path() -> str:
    # macOS limits AF_UNIX paths to ~104 chars, so pytest's tmp_path is too long.
    return os.path.join(tempfile.gettempdir(), f"cs-{uuid.uuid4().hex[:8]}.sock")


def test_read_activity_log_returns_recent_lines(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "activity.jsonl"
    log.write_text(
        '{"ts":"2026-05-22T10:00:00Z","tool":"Read","session":"s-1"}\n'
        '{"ts":"2026-05-22T10:00:01Z","tool":"Edit","session":"s-1"}\n'
    )
    monkeypatch.setenv("HERMES_ACTIVITY_LOG", str(log))
    rows = read_activity_log(limit=10)
    assert len(rows) == 2
    assert rows[0]["tool"] == "Read"


def test_read_memory_returns_text(tmp_path: Path, monkeypatch) -> None:
    proj_dir = tmp_path / "encoded-proj"
    (proj_dir / "memory").mkdir(parents=True)
    (proj_dir / "memory" / "MEMORY.md").write_text("- thing\n- other\n")
    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(tmp_path))
    result = read_memory("encoded-proj")
    assert "thing" in result["text"]


def test_list_sessions_returns_empty_when_no_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CLAUDE_JOBS_ROOT", str(tmp_path))
    assert list_sessions() == []


async def test_socket_round_trip_returns_result(tmp_path: Path) -> None:
    sock_path = _short_sock_path()

    async def echo_handler(params: dict) -> dict:
        return {"got": params}

    handlers = {"echo": echo_handler}
    server_task = asyncio.create_task(_serve(handlers, sock_path))
    try:
        for _ in range(50):
            if Path(sock_path).exists():
                break
            await asyncio.sleep(0.01)
        result = await call("echo", {"hello": "world"}, sock_path=sock_path)
        assert result == {"got": {"hello": "world"}}
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        Path(sock_path).unlink(missing_ok=True)


async def test_socket_round_trip_handles_large_response(tmp_path: Path) -> None:
    sock_path = _short_sock_path()

    big_items = ["x" * 100 for _ in range(2000)]

    async def big_handler(_params: dict) -> dict:
        return {"items": big_items}

    handlers = {"big": big_handler}
    server_task = asyncio.create_task(_serve(handlers, sock_path))
    try:
        for _ in range(50):
            if Path(sock_path).exists():
                break
            await asyncio.sleep(0.01)
        result = await call("big", {}, sock_path=sock_path)
        assert len(result["items"]) == 2000
        assert sum(len(s) for s in result["items"]) == 200000
        assert result["items"] == big_items
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        Path(sock_path).unlink(missing_ok=True)


async def test_socket_round_trip_returns_error_on_unknown_method(tmp_path: Path) -> None:
    sock_path = _short_sock_path()

    async def echo_handler(params: dict) -> dict:
        return {"got": params}

    handlers = {"echo": echo_handler}
    server_task = asyncio.create_task(_serve(handlers, sock_path))
    try:
        for _ in range(50):
            if Path(sock_path).exists():
                break
            await asyncio.sleep(0.01)
        raised = False
        try:
            await call("does_not_exist", {}, sock_path=sock_path)
        except RuntimeError as e:
            raised = True
            assert "does_not_exist" in str(e)
        assert raised, "expected RuntimeError for unknown method"
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        Path(sock_path).unlink(missing_ok=True)


# ---- schedule_reminder tests -----------------------------------------------

def _make_fake_proc(pid: int = 12345) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    return proc


def test_schedule_reminder_relative_5m(monkeypatch) -> None:
    """Relative '5m' → bash script contains 'sleep 300'."""
    fake_proc = _make_fake_proc(42)
    monkeypatch.setenv("HERMES_NOTIFY_CHAT_ID", "999")

    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        captured["env"] = kwargs.get("env", {})
        return fake_proc

    with patch("claude_soma.mcp_servers.hermes_api.server.subprocess.Popen", fake_popen):
        result = schedule_reminder("5m", "test message")

    assert result["pid"] == 42
    assert "fires_at_iso" in result
    assert "message_preview" in result
    # The bash script passed to bash -c must contain sleep 300
    bash_script = captured["cmd"][2]
    assert "sleep 300" in bash_script


def test_schedule_reminder_iso_timestamp(monkeypatch) -> None:
    """ISO 8601 timestamp in the future → delay is computed correctly."""
    monkeypatch.setenv("HERMES_NOTIFY_CHAT_ID", "999")
    fake_proc = _make_fake_proc(99)

    # 1 hour from now
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    iso_str = future.strftime("%Y-%m-%dT%H:%M:%SZ")

    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return fake_proc

    with patch("claude_soma.mcp_servers.hermes_api.server.subprocess.Popen", fake_popen):
        result = schedule_reminder(iso_str, "iso reminder")

    bash_script = captured["cmd"][2]
    # Delay should be approximately 3600 seconds; extract the sleep value
    import re as _re
    m = _re.search(r"sleep (\d+)", bash_script)
    assert m is not None, "bash script should contain a sleep command"
    sleep_secs = int(m.group(1))
    assert 3500 <= sleep_secs <= 3660, f"Expected ~3600s delay, got {sleep_secs}"
    assert result["fires_at_iso"].endswith("Z")


def test_schedule_reminder_returns_pid(monkeypatch) -> None:
    """Return dict has pid, fires_at_iso, and message_preview."""
    monkeypatch.setenv("HERMES_NOTIFY_CHAT_ID", "135")
    fake_proc = _make_fake_proc(7777)

    with patch(
        "claude_soma.mcp_servers.hermes_api.server.subprocess.Popen",
        return_value=fake_proc,
    ):
        result = schedule_reminder("10m", "a" * 200)

    assert result["pid"] == 7777
    assert isinstance(result["fires_at_iso"], str)
    assert "T" in result["fires_at_iso"]
    # message_preview is capped at 100 chars
    assert len(result["message_preview"]) == 100


def test_schedule_reminder_registers_routine(tmp_path, monkeypatch) -> None:
    """schedule_reminder must write a registry row so /api/routines shows it."""
    from pathlib import Path as _Path
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_NOTIFY_CHAT_ID", "135")
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    fake_proc = _make_fake_proc(9001)

    with patch(
        "claude_soma.mcp_servers.hermes_api.server.subprocess.Popen",
        return_value=fake_proc,
    ):
        result = schedule_reminder("15m", "buy milk")

    from claude_soma.mcp_servers.project_orchestrator.registry import Registry
    reg = Registry(db)
    try:
        routines = reg.list_routines()
    finally:
        reg.close()

    assert len(routines) == 1
    row = routines[0]
    assert row["name"].startswith("reminder-")
    assert row["kind"] == "local"
    assert row["created_by"] == "user"
    assert row["metadata"] is not None
    assert row["metadata"]["unit"].startswith("reminder-")
    assert row["metadata"]["pid"] == 9001
    assert "buy milk" in (row["description"] or "")


# ---- routines cache prewarm tests ------------------------------------------

def test_routines_cache_prewarm_on_startup() -> None:
    """_prewarm_routines_cache() calls _query_cloud_routines_cached exactly once."""
    call_count: list[int] = [0]

    def fake_query():
        call_count[0] += 1
        return []

    with patch(
        "claude_soma.api.routes.routines._query_cloud_routines_cached",
        side_effect=fake_query,
    ):
        _prewarm_routines_cache()

    assert call_count[0] == 1, "prewarm should call _query_cloud_routines_cached once"


def test_routines_cache_prewarm_on_timer_fire() -> None:
    """Direct _prewarm_routines_cache() call populates the cloud cache."""
    from claude_soma.api.routes.routines import _CLOUD_CACHE, _clear_routines_cache

    _clear_routines_cache()
    assert not _CLOUD_CACHE["valid"], "cache should start invalid"

    fake_rows = [{"name": "fake-routine", "kind": "cloud", "schedule": "*/5 * * * *"}]

    with patch(
        "claude_soma.api.routes.routines._query_cloud_routines",
        return_value=fake_rows,
    ):
        _prewarm_routines_cache()

    assert _CLOUD_CACHE["valid"], "cache should be valid after prewarm"
    assert _CLOUD_CACHE["rows"] == fake_rows
