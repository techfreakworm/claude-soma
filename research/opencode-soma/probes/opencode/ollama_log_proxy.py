"""Logging reverse proxy between opencode and ollama (stdlib only, 127.0.0.1 only).

Per request it appends one JSON line to --log with: time, path, model, tool names offered,
system-prompt size, message count/roles, the last user message (truncated), selected request
params, then (after the response finished) the usage block, finish reason, tool calls the
model emitted, response text (truncated) and wall-clock. The response itself is streamed
back to the client untouched, chunk by chunk.
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import socketserver
import threading
import time
import urllib.parse

LOCK = threading.Lock()
TRUNC = 600


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text", "")))
        return "\n".join(parts)
    return ""


def summarize_request(body: bytes) -> dict[str, object]:
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        return {"unparsed_bytes": len(body)}
    msgs = req.get("messages") or []
    system = "\n".join(_text_of(m.get("content")) for m in msgs if m.get("role") == "system")
    users = [m for m in msgs if m.get("role") == "user"]
    tools = [
        (t.get("function") or {}).get("name") or t.get("name")
        for t in (req.get("tools") or [])
    ]
    last_user = _text_of(users[-1].get("content")) if users else ""
    tool_results = [m for m in msgs if m.get("role") == "tool"]
    return {
        "model": req.get("model"),
        "stream": req.get("stream"),
        "params": {
            k: req.get(k)
            for k in ("temperature", "top_p", "max_tokens", "reasoning_effort", "tool_choice")
            if k in req
        },
        "n_messages": len(msgs),
        "roles": [m.get("role") for m in msgs],
        "system_chars": len(system),
        "system_head": system[:300],
        "system_tail": system[-400:],
        "tools_offered": tools,
        "n_tools": len(tools),
        "tools_json_chars": len(json.dumps(req.get("tools") or [])),
        "last_user": last_user[-TRUNC:],
        "last_tool_result": _text_of(tool_results[-1].get("content"))[-TRUNC:]
        if tool_results
        else None,
    }


class Accum:
    """Re-assembles an OpenAI-style SSE stream (or a plain JSON body) for the log line."""

    def __init__(self) -> None:
        self.buf = b""
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict[str, str]] = {}
        self.usage: object = None
        self.finish: object = None
        self.raw_json: object = None

    def feed_sse(self, chunk: bytes) -> None:
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                continue
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            self._event(ev)

    def _event(self, ev: dict) -> None:  # type: ignore[type-arg]
        if ev.get("usage"):
            self.usage = ev["usage"]
        for ch in ev.get("choices") or []:
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
            d = ch.get("delta") or ch.get("message") or {}
            if d.get("content"):
                self.text.append(d["content"])
            r = d.get("reasoning") or d.get("reasoning_content")
            if r:
                self.reasoning.append(r)
            for tc in d.get("tool_calls") or []:
                i = tc.get("index", 0)
                slot = self.tool_calls.setdefault(i, {"name": "", "arguments": ""})
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

    def feed_json(self, body: bytes) -> None:
        try:
            ev = json.loads(body)
        except ValueError:
            return
        if isinstance(ev, dict):
            self._event(ev)

    def summary(self) -> dict[str, object]:
        return {
            "finish_reason": self.finish,
            "usage": self.usage,
            "tool_calls": [
                {"name": v["name"], "arguments": v["arguments"][:TRUNC]}
                for _, v in sorted(self.tool_calls.items())
            ],
            "text": "".join(self.text)[:TRUNC],
            "reasoning_chars": len("".join(self.reasoning)),
        }


def make_handler(upstream: str, log_path: str):  # type: ignore[no-untyped-def]
    up = urllib.parse.urlparse(upstream)

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _proxy(self) -> None:
            if self.path == "/__proxy_health":
                body = b"ok"
                self.send_response(200)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            t0 = time.time()
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            entry: dict[str, object] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                "method": self.command,
                "path": self.path,
                "client_user_agent": self.headers.get("user-agent"),
            }
            if self.command == "POST" and body:
                entry["request"] = summarize_request(body)
            conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=3600)
            hdrs = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "connection", "accept-encoding")
            }
            hdrs["content-length"] = str(len(body))
            conn.request(self.command, self.path, body=body, headers=hdrs)
            resp = conn.getresponse()
            ctype = resp.getheader("content-type") or ""
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("transfer-encoding", "chunked")
            self.send_header("connection", "close")
            self.end_headers()
            acc = Accum()
            is_sse = "event-stream" in ctype
            whole = b""
            t_first = None
            while True:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                if not chunk:
                    break
                if t_first is None:
                    t_first = time.time()
                if is_sse:
                    acc.feed_sse(chunk)
                else:
                    whole += chunk
                try:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    entry["client_disconnected"] = True
                    break
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                entry["client_disconnected"] = True
            if not is_sse and whole:
                if self.path.endswith("/chat/completions"):
                    acc.feed_json(whole)
                else:
                    entry["response_bytes"] = len(whole)
            entry["status"] = resp.status
            entry["response"] = acc.summary()
            entry["ttfb_s"] = round((t_first or time.time()) - t0, 2)
            entry["wall_s"] = round(time.time() - t0, 2)
            with LOCK, open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.close_connection = True

        do_GET = _proxy
        do_POST = _proxy
        do_DELETE = _proxy
        do_PUT = _proxy

    return H


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--upstream", default="http://127.0.0.1:11434")
    a = ap.parse_args()
    # Allowed window = [OCSOMA_PORT_BASE, base+99]; default base 47100 keeps probe-engineer runs in
    # 47100-47199, and the verifier (OCSOMA_PORT_BASE=47300) stays in its own 47300-47399 range.
    base = int(os.environ.get("OCSOMA_PORT_BASE", "47100"))
    if not base <= a.port <= base + 99:
        raise SystemExit(f"port {a.port} outside the allowed [{base}, {base + 99}] range")
    srv = Server(("127.0.0.1", a.port), make_handler(a.upstream, a.log))
    srv.serve_forever()


if __name__ == "__main__":
    main()
