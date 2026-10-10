"""Minimal MCP server, stdlib only, for the opencode probes.

  --transport stdio            newline-delimited JSON-RPC on stdin/stdout
  --transport http --port N    streamable-HTTP (JSON responses) on 127.0.0.1:N/mcp

--kind picks the tool set:
  echo-stdio  -> tool `stdio_echo(text)`  returns "STDIO-MCP-OK:<text>"
  echo-http   -> tool `http_echo(text)`   returns "HTTP-MCP-OK:<text>"
  telegram    -> tool `send_message(chat_id, text)`; appends to --outbox and returns "TELEGRAM-SENT"
Every request is appended to --log (JSONL) so a probe can prove which calls actually arrived.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import time
import uuid

PROTOCOL_FALLBACK = "2025-06-18"

KINDS = {
    "echo-stdio": [
        {
            "name": "stdio_echo",
            "description": "Echo text back from the STDIO probe MCP server.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "text to echo"}},
                "required": ["text"],
            },
        }
    ],
    "echo-http": [
        {
            "name": "http_echo",
            "description": "Echo text back from the HTTP probe MCP server.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "text to echo"}},
                "required": ["text"],
            },
        }
    ],
    "telegram": [
        {
            "name": "send_message",
            "description": "Send a Telegram message to the operator (FAKE probe server).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string", "description": "chat id"},
                    "text": {"type": "string", "description": "message text"},
                },
                "required": ["text"],
            },
        }
    ],
}


class Srv:
    def __init__(self, kind: str, log: str, outbox: str | None) -> None:
        self.kind = kind
        self.log_path = log
        self.outbox = outbox

    def log(self, entry: dict[str, object]) -> None:
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry["kind"] = self.kind
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def call(self, name: str, args: dict[str, object]) -> dict[str, object]:
        if self.kind == "echo-stdio" and name == "stdio_echo":
            return {"content": [{"type": "text", "text": f"STDIO-MCP-OK:{args.get('text', '')}"}]}
        if self.kind == "echo-http" and name == "http_echo":
            return {"content": [{"type": "text", "text": f"HTTP-MCP-OK:{args.get('text', '')}"}]}
        if self.kind == "telegram" and name == "send_message":
            if self.outbox:
                with open(self.outbox, "a", encoding="utf-8") as f:
                    f.write(json.dumps(args) + "\n")
            return {"content": [{"type": "text", "text": "TELEGRAM-SENT"}]}
        return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}

    def handle(self, msg: dict[str, object]) -> dict[str, object] | None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        assert isinstance(params, dict)
        self.log({"method": method, "id": mid, "params": params})
        if mid is None:
            return None
        if method == "initialize":
            pv = params.get("protocolVersion") or PROTOCOL_FALLBACK
            result: dict[str, object] = {
                "protocolVersion": pv,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"probe-{self.kind}", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": KINDS[self.kind]}
        elif method == "tools/call":
            args = params.get("arguments") or {}
            assert isinstance(args, dict)
            result = self.call(str(params.get("name")), args)
        elif method == "ping":
            result = {}
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        else:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def run_stdio(srv: Srv) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        for m in msgs:
            resp = srv.handle(m)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()


def run_http(srv: Srv, port: int) -> None:
    session_id = uuid.uuid4().hex

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *a: object) -> None:
            return

        def _send(self, code: int, body: bytes = b"", ctype: str | None = None) -> None:
            self.send_response(code)
            if ctype:
                self.send_header("content-type", ctype)
            self.send_header("mcp-session-id", session_id)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path.split("?")[0] != "/mcp":
                self._send(404)
                return
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n)
            try:
                msg = json.loads(raw)
            except ValueError:
                self._send(400)
                return
            msgs = msg if isinstance(msg, list) else [msg]
            out = [r for r in (srv.handle(m) for m in msgs) if r is not None]
            if not out:
                self._send(202)
                return
            body = json.dumps(out if isinstance(msg, list) else out[0]).encode()
            self._send(200, body, "application/json")

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, b"ok", "text/plain")
                return
            srv.log({"method": "GET-sse-stream", "path": self.path})
            self._send(405)

        def do_DELETE(self) -> None:
            self._send(200)

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    if not 47100 <= port <= 47199:
        raise SystemExit("port outside 47100-47199")
    S(("127.0.0.1", port), H).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], required=True)
    ap.add_argument("--kind", choices=sorted(KINDS), required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--outbox")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    srv = Srv(a.kind, a.log, a.outbox)
    if a.transport == "stdio":
        run_stdio(srv)
    else:
        run_http(srv, a.port)


if __name__ == "__main__":
    main()
