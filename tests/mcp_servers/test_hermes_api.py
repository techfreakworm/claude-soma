from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

from claude_soma.mcp_servers.hermes_api.claude_state import (
    list_sessions, read_activity_log, read_memory
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
    text = read_memory("encoded-proj")
    assert "thing" in text


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
