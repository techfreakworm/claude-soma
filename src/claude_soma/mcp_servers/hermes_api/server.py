from __future__ import annotations

import threading

from mcp.server.fastmcp import FastMCP

from . import claude_state
from .socket import serve_blocking


# ---- MCP tools exposed to the channel session ------------------------------

mcp = FastMCP("hermes_api")


@mcp.tool()
def list_active_sessions() -> list[dict]:
    """List background Claude sessions managed by the agent view supervisor."""
    return claude_state.list_sessions()


@mcp.tool()
def read_activity_log(limit: int = 200) -> list[dict]:
    """Read recent PostToolUse activity log lines."""
    return claude_state.read_activity_log(limit)


@mcp.tool()
def read_memory(project_slug: str) -> str:
    """Read MEMORY.md for a given Claude project slug."""
    return claude_state.read_memory(project_slug)


@mcp.tool()
def list_transcript_threads(limit: int = 50) -> list[dict]:
    """List recent transcript threads across all projects."""
    return claude_state.list_transcript_threads(limit)


@mcp.tool()
def read_transcript(thread_id: str, project: str) -> list[dict]:
    """Read a transcript as list of message events."""
    return claude_state.read_transcript(thread_id, project)


# ---- Unix-socket bridge for the FastAPI dashboard backend -----------------

async def _h_list_sessions(_p: dict) -> dict:
    return {"items": claude_state.list_sessions()}


async def _h_read_activity(p: dict) -> dict:
    return {"items": claude_state.read_activity_log(p.get("limit", 200))}


async def _h_read_memory(p: dict) -> dict:
    return {"text": claude_state.read_memory(p["project_slug"])}


async def _h_list_threads(p: dict) -> dict:
    return {"items": claude_state.list_transcript_threads(p.get("limit", 50))}


async def _h_read_transcript(p: dict) -> dict:
    return {"items": claude_state.read_transcript(p["thread_id"], p["project"])}


HANDLERS = {
    "list_sessions": _h_list_sessions,
    "read_activity_log": _h_read_activity,
    "read_memory": _h_read_memory,
    "list_threads": _h_list_threads,
    "read_transcript": _h_read_transcript,
}


def _start_socket_server() -> None:
    serve_blocking(HANDLERS)


def main() -> None:
    # Run the unix socket server in a background thread so the FastAPI bridge
    # can call into our state without going through MCP.
    t = threading.Thread(target=_start_socket_server, daemon=True)
    t.start()
    mcp.run()


if __name__ == "__main__":
    main()
