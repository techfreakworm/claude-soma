from __future__ import annotations

from typing import Any

from claude_soma.mcp_servers.hermes_api import socket as hermes_socket


async def call_hermes(method: str, params: dict[str, Any] | None = None) -> Any:
    try:
        return await hermes_socket.call(method, params or {})
    except (FileNotFoundError, ConnectionRefusedError):
        return {"items": []} if "list" in method or method.endswith("threads") else {}
