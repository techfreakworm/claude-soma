"""Step 7 cross-host FI-NOTIFY: listener-as-writer + bearer token + the notify
MCP as a pure HTTP client. Pure unit tests (no live ssh / no real listener)."""
from __future__ import annotations

import json

from claude_soma.mcp_servers.hermes_api import server as s
from claude_soma.mcp_servers.hermes_api.notify_store import EventStore


def test_check_bearer():
    assert s._check_bearer("Bearer abc", "abc") is True
    assert s._check_bearer("Bearer abc", "xyz") is False
    assert s._check_bearer(None, "abc") is False
    assert s._check_bearer("abc", "abc") is False  # no 'Bearer ' prefix


def test_ingest_event_owns_id(tmp_path):
    s._store = EventStore(str(tmp_path / "r.sqlite"))
    eid = s._ingest_event("test-b", "MILESTONE", json.dumps({"progress": "x"}))
    assert isinstance(eid, int) and eid >= 1
    row = s._store.get_event(eid)
    assert row and row["lead"] == "test-b" and row["type"] == "MILESTONE"


def test_ingest_needs_input_creates_companion(tmp_path):
    s._store = EventStore(str(tmp_path / "r.sqlite"))
    pj = json.dumps({"question": "proceed?", "options": ["y", "n"]})
    eid = s._ingest_event("test-b", "NEEDS_INPUT", pj)
    opens = s._store.get_open_pending_inputs(limit=5)
    assert any(p["event_id"] == eid for p in opens)  # companion created on A


def test_notify_mcp_posts_raw_and_returns_listener_id(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    captured = {}

    def fake_post(url, obj):
        captured["url"] = url
        captured["obj"] = obj
        return True, {"event_id": 4242}

    monkeypatch.setattr(n, "_post_json", fake_post)
    monkeypatch.setenv("HERMES_LEAD_NAME", "test-b")
    out = n.notify_orchestrator("MILESTONE", {"progress": "hi"})
    assert out == {"stored_id": 4242, "delivered": True}
    assert captured["obj"]["lead"] == "test-b"
    assert captured["obj"]["type"] == "MILESTONE"
    assert "event_id" not in captured["obj"]  # client never supplies the id


def test_post_json_sets_bearer(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    monkeypatch.setenv("HERMES_NOTIFY_TOKEN", "secret")
    seen = {}

    class R:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=10):
        seen["auth"] = req.headers.get("Authorization")
        return R()

    monkeypatch.setattr(n.urllib.request, "urlopen", fake_urlopen)
    n._post_json("http://127.0.0.1:9100/notify",
                 {"lead": "x", "type": "STARTED", "payload_json": "{}"})
    assert seen["auth"] == "Bearer secret"


def test_listener_url_default_and_override(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    monkeypatch.delenv("HERMES_NOTIFY_URL", raising=False)
    assert n._listener_url().endswith("/notify")
    monkeypatch.setenv("HERMES_NOTIFY_URL", "http://100.103.37.115:9100/notify")
    assert n._listener_url() == "http://100.103.37.115:9100/notify"
    assert n._listener_base() == "http://100.103.37.115:9100"


# --- degraded-webhook fallback (A-down safety net for remote financial leads) ---

def test_degraded_fires_on_connection_error(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    calls = {}
    monkeypatch.setattr(n, "_maybe_degraded_alert", lambda obj: calls.setdefault("obj", obj))

    def boom(req, timeout=10):
        raise n.urllib.error.URLError("Connection refused")

    monkeypatch.setattr(n.urllib.request, "urlopen", boom)
    ok, _ = n._post_json("http://127.0.0.1:9100/notify", {"lead": "x", "type": "MILESTONE"})
    assert ok is False
    assert calls.get("obj", {}).get("lead") == "x"  # degraded path invoked


def test_degraded_skipped_on_http_error(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    calls = {}
    monkeypatch.setattr(n, "_maybe_degraded_alert", lambda obj: calls.setdefault("hit", True))

    def http_err(req, timeout=10):
        raise n.urllib.error.HTTPError("http://a", 401, "no", {}, None)

    monkeypatch.setattr(n.urllib.request, "urlopen", http_err)
    ok, _ = n._post_json("http://127.0.0.1:9100/notify", {"lead": "x", "type": "MILESTONE"})
    assert ok is False
    assert "hit" not in calls  # A responded (up) => no degraded fallback


def test_degraded_noop_without_webhook(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    monkeypatch.delenv("HERMES_DEGRADED_WEBHOOK", raising=False)
    posted = {}
    monkeypatch.setattr(n.urllib.request, "urlopen",
                        lambda req, timeout=8: posted.setdefault("hit", True))
    n._maybe_degraded_alert({"lead": "x", "type": "MILESTONE", "payload_json": "{}"})
    assert "hit" not in posted  # no webhook configured => no outbound post


def test_degraded_posts_to_webhook_when_set(monkeypatch):
    from claude_soma.mcp_servers.hermes_notify import server as n
    monkeypatch.setenv("HERMES_DEGRADED_WEBHOOK", "https://discord.com/api/webhooks/x")
    seen = {}

    class R:
        def close(self): pass

    def fake(req, timeout=8):
        seen["url"] = req.full_url
        seen["body"] = req.data
        seen["ua"] = req.headers.get("User-agent")
        return R()

    monkeypatch.setattr(n.urllib.request, "urlopen", fake)
    n._maybe_degraded_alert({"lead": "algo-trader", "type": "ERROR",
                             "payload_json": '{"error":"feed down"}'})
    assert seen["url"].startswith("https://discord.com/api/webhooks/")
    assert b"DEGRADED" in seen["body"] and b"algo-trader" in seen["body"]
    assert seen["ua"]  # UA present (Discord/Cloudflare)
