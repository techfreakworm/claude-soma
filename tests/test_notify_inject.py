from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "notify_inject.sh"


def _run_hook(env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    combined_env = os.environ.copy()
    if env:
        combined_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        input="{}",
        capture_output=True,
        text=True,
        timeout=10,
        env=combined_env,
    )


def test_notify_inject_exits_zero_when_listener_down() -> None:
    """If listener is unreachable (bad port), script exits 0 with no output."""
    result = _run_hook({"HERMES_NOTIFY_PORT": "19999"})
    assert result.returncode == 0


def test_notify_inject_exits_zero_with_empty_json_on_no_events() -> None:
    """When curl fails (no server), output should be empty or valid JSON."""
    result = _run_hook({"HERMES_NOTIFY_PORT": "19999"})
    assert result.returncode == 0
    # Either empty output (fail-open) or valid JSON
    if result.stdout.strip():
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)


def test_notify_inject_script_is_valid_bash() -> None:
    """bash -n should report no syntax errors."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_notify_inject_output_schema_with_mock_server(tmp_path: Path) -> None:
    """Mock the HTTP server and verify the hook emits correct additionalContext JSON."""
    import http.server
    import threading
    import socket

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    mock_response = {
        "events": [
            {
                "id": 1,
                "lead": "f1-tracker",
                "type": "COMPLETED",
                "ts": time.time(),
                "payload_json": json.dumps({"summary": "Done with race data!"}),
                "created_at": time.time(),
                "delivered_at": None,
                "delivery_error": None,
                "hook_injected_at": None,
            }
        ],
        "open_pending_inputs": [],
    }

    mark_read_called = []

    class MockHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            data = json.dumps(mock_response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            mark_read_called.append(json.loads(body))
            data = b'{"marked":1}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", free_port), MockHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        result = _run_hook({"HERMES_NOTIFY_PORT": str(free_port)})
        assert result.returncode == 0, f"hook exited non-zero: stderr={result.stderr}"

        output = result.stdout.strip()
        assert output, f"expected non-empty output, got: {result.stderr!r}"

        parsed = json.loads(output)
        assert "hookSpecificOutput" in parsed
        hook_out = parsed["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in hook_out
        ctx = hook_out["additionalContext"]
        assert "lead events" in ctx.lower() or "f1-tracker" in ctx

        # mark_read should have been called
        time.sleep(0.1)
        assert len(mark_read_called) >= 1
        assert mark_read_called[0]["event_ids"] == [1]
    finally:
        srv.shutdown()


def test_notify_inject_needs_input_appears_in_context(tmp_path: Path) -> None:
    """OPEN NEEDS_INPUT should appear in additionalContext."""
    import http.server
    import threading
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    mock_response = {
        "events": [],
        "open_pending_inputs": [
            {
                "id": 1,
                "event_id": 42,
                "lead": "social-publisher",
                "question": "Newsletter or regular post?",
                "options": ["newsletter", "regular post"],
                "status": "open",
                "created_at": time.time(),
            }
        ],
    }

    class MockHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            data = json.dumps(mock_response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            data = b'{"marked":0}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", free_port), MockHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        result = _run_hook({"HERMES_NOTIFY_PORT": str(free_port)})
        assert result.returncode == 0
        output = result.stdout.strip()
        assert output
        parsed = json.loads(output)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "NEEDS_INPUT" in ctx or "needs_input" in ctx.lower() or "social-publisher" in ctx
        assert "event_id=42" in ctx
    finally:
        srv.shutdown()


def test_notify_inject_empty_events_emits_empty_object() -> None:
    """When the server returns 0 events and 0 pending inputs, emit '{}'."""
    import http.server
    import threading
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    class MockHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            data = b'{"events": [], "open_pending_inputs": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", free_port), MockHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        result = _run_hook({"HERMES_NOTIFY_PORT": str(free_port)})
        assert result.returncode == 0
        output = result.stdout.strip()
        parsed = json.loads(output)
        assert parsed == {}
    finally:
        srv.shutdown()
