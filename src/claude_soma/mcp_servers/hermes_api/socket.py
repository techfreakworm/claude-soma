from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Awaitable


SOCKET_PATH = os.environ.get(
    "HERMES_API_SOCKET", "/tmp/claude-soma-api.sock"
)


HandlerMap = dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]


async def _serve(handlers: HandlerMap, sock_path: str) -> None:
    if Path(sock_path).exists():
        Path(sock_path).unlink()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await reader.readline()
            req = json.loads(data)
            method = req.get("method", "")
            params = req.get("params", {})
            handler = handlers.get(method)
            if handler is None:
                resp = {"error": f"unknown method {method!r}"}
            else:
                try:
                    resp = {"result": await handler(params)}
                except Exception as e:
                    resp = {"error": str(e)}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=sock_path)
    os.chmod(sock_path, 0o600)
    async with server:
        await server.serve_forever()


def serve_blocking(handlers: HandlerMap, sock_path: str = SOCKET_PATH) -> None:
    asyncio.run(_serve(handlers, sock_path))


async def call(method: str, params: dict[str, Any] | None = None,
               sock_path: str = SOCKET_PATH) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(sock_path)
    try:
        req = {"method": method, "params": params or {}}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        data = await reader.readline()
        resp = json.loads(data)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]
    finally:
        writer.close()
        await writer.wait_closed()
