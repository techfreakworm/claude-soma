from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Awaitable


_DEFAULT_SOCKET_PATH = "/tmp/claude-soma-api.sock"

# Backward-compat constant; the effective path is resolved at call time.
SOCKET_PATH = os.environ.get("HERMES_API_SOCKET", _DEFAULT_SOCKET_PATH)


HandlerMap = dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]


async def _serve(handlers: HandlerMap, sock_path: str) -> None:
    if Path(sock_path).exists():
        Path(sock_path).unlink()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                data = await reader.readline()
                req = json.loads(data)
                method = req.get("method", "")
                params = req.get("params", {})
                handler = handlers.get(method)
                if handler is None:
                    resp = {"error": f"unknown method {method!r}"}
                else:
                    resp = {"result": await handler(params)}
            except Exception as e:
                resp = {"error": f"{type(e).__name__}: {e}"}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=sock_path)
    os.chmod(sock_path, 0o600)
    async with server:
        await server.serve_forever()


def _resolve_sock_path(sock_path: str | None) -> str:
    if sock_path is not None:
        return sock_path
    return os.environ.get("HERMES_API_SOCKET", _DEFAULT_SOCKET_PATH)


def serve_blocking(handlers: HandlerMap, sock_path: str | None = None) -> None:
    asyncio.run(_serve(handlers, _resolve_sock_path(sock_path)))


async def call(method: str, params: dict[str, Any] | None = None,
               sock_path: str | None = None) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(_resolve_sock_path(sock_path))
    try:
        req = {"method": method, "params": params or {}}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        # Read to EOF: the server writes one full response then closes, so this
        # returns the complete payload with no readline() 64 KB line limit.
        data = await reader.read()
        resp = json.loads(data)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]
    finally:
        writer.close()
        await writer.wait_closed()
