from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_soma.mcp_servers.hermes_api.notify_store import (
    EventStore,
    VALID_TYPES,
    URGENT_TYPES,
)


# ------------------------------------------------------------------ fixtures

@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    db = tmp_path / "test_registry.sqlite"
    return EventStore(db_path=db)


# ------------------------------------------------------------------ notify_store unit tests

def test_insert_event_returns_positive_id(store: EventStore) -> None:
    event_id = store.insert_event(
        lead="test-lead",
        type_="STARTED",
        ts=time.time(),
        payload_json='{"description": "smoke test"}',
    )
    assert isinstance(event_id, int)
    assert event_id > 0


def test_get_event_returns_row(store: EventStore) -> None:
    eid = store.insert_event(
        lead="test-lead",
        type_="COMPLETED",
        ts=time.time(),
        payload_json='{"summary": "done"}',
    )
    row = store.get_event(eid)
    assert row is not None
    assert row["lead"] == "test-lead"
    assert row["type"] == "COMPLETED"


def test_mark_delivered_sets_timestamp(store: EventStore) -> None:
    eid = store.insert_event(
        lead="lead-a",
        type_="STARTED",
        ts=time.time(),
        payload_json='{"description": "x"}',
    )
    assert store.get_event(eid)["delivered_at"] is None
    store.mark_delivered(eid)
    assert store.get_event(eid)["delivered_at"] is not None


def test_mark_delivery_error_stores_message(store: EventStore) -> None:
    eid = store.insert_event(
        lead="lead-a",
        type_="ERROR",
        ts=time.time(),
        payload_json='{"error": "x", "context": "y"}',
    )
    store.mark_delivery_error(eid, "connection refused")
    row = store.get_event(eid)
    assert row["delivery_error"] == "connection refused"
    assert row["delivered_at"] is None


def test_get_undelivered_urgent_returns_only_urgent(store: EventStore) -> None:
    ts = time.time()
    eid_started = store.insert_event(
        lead="l", type_="STARTED", ts=ts, payload_json='{"description":"x"}'
    )
    eid_completed = store.insert_event(
        lead="l", type_="COMPLETED", ts=ts, payload_json='{"summary":"done"}'
    )
    eid_error = store.insert_event(
        lead="l", type_="ERROR", ts=ts, payload_json='{"error":"x","context":"y"}'
    )
    # deliver started — should not appear in urgent drain
    store.mark_delivered(eid_started)

    urgent = store.get_undelivered_urgent()
    ids = [r["id"] for r in urgent]
    assert eid_completed in ids
    assert eid_error in ids
    assert eid_started not in ids


def test_get_uninjected_returns_rows_and_marks_hook_injected(store: EventStore) -> None:
    ts = time.time()
    eid1 = store.insert_event(
        lead="l", type_="STARTED", ts=ts, payload_json='{"description":"a"}'
    )
    eid2 = store.insert_event(
        lead="l", type_="MILESTONE", ts=ts, payload_json='{"progress":"b"}'
    )
    rows = store.get_uninjected(limit=10)
    assert len(rows) == 2
    store.mark_hook_injected([eid1, eid2])
    rows2 = store.get_uninjected(limit=10)
    assert len(rows2) == 0


def test_insert_needs_input_creates_pending_row(store: EventStore) -> None:
    ts = time.time()
    eid, pid = store.insert_event_with_pending_input(
        lead="lead-q",
        ts=ts,
        payload_json='{"question":"Yes or no?","options":["yes","no"]}',
        question="Yes or no?",
        options_json='["yes","no"]',
        timeout_secs=120,
    )
    assert eid > 0
    assert pid > 0
    pending = store.get_open_pending_inputs()
    assert len(pending) == 1
    assert pending[0]["question"] == "Yes or no?"
    assert pending[0]["lead"] == "lead-q"


def test_mark_pending_resolved_closes_row(store: EventStore) -> None:
    ts = time.time()
    eid, _pid = store.insert_event_with_pending_input(
        lead="lead-q",
        ts=ts,
        payload_json='{"question":"x?"}',
        question="x?",
        options_json=None,
        timeout_secs=None,
    )
    result = store.mark_pending_resolved(eid, "answer text")
    assert result is True
    pending = store.get_open_pending_inputs()
    assert len(pending) == 0


def test_mark_pending_resolved_returns_false_for_unknown(store: EventStore) -> None:
    result = store.mark_pending_resolved(9999, "anything")
    assert result is False


def test_milestone_throttle_data(store: EventStore) -> None:
    ts = time.time()
    eid = store.insert_event(
        lead="l", type_="MILESTONE", ts=ts, payload_json='{"progress":"x"}'
    )
    store.mark_delivered(eid)
    times = store.get_milestone_last_delivered_times()
    assert "l" in times
    assert isinstance(times["l"], float)


def test_get_undelivered_milestones(store: EventStore) -> None:
    ts = time.time()
    eid1 = store.insert_event(
        lead="l", type_="MILESTONE", ts=ts, payload_json='{"progress":"step1"}'
    )
    _eid2 = store.insert_event(
        lead="l", type_="MILESTONE", ts=ts, payload_json='{"progress":"step2"}'
    )
    store.mark_delivered(eid1)  # only eid1 is delivered
    pending = store.get_undelivered_milestones("l")
    assert len(pending) == 1
    assert pending[0]["payload_json"] == '{"progress":"step2"}'


def test_valid_types_constant() -> None:
    assert VALID_TYPES == frozenset({"STARTED", "MILESTONE", "COMPLETED", "NEEDS_INPUT", "ERROR"})


def test_urgent_types_constant() -> None:
    assert URGENT_TYPES == frozenset({"COMPLETED", "NEEDS_INPUT", "ERROR"})


# ------------------------------------------------------------------ hermes_notify MCP tool unit tests

def test_notify_orchestrator_missing_lead_name_raises(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": ""}, clear=False):
        hn_server._store = EventStore(db_path=tmp_path / "r.sqlite")
        with pytest.raises((ValueError, Exception), match="HERMES_LEAD_NAME"):
            hn_server.notify_orchestrator(
                type="STARTED",
                payload={"description": "test"},
            )


def test_notify_orchestrator_invalid_type_raises(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "test-lead"}, clear=False):
        hn_server._store = EventStore(db_path=tmp_path / "r.sqlite")
        with pytest.raises((ValueError, Exception)):
            hn_server.notify_orchestrator(
                type="UNKNOWN_TYPE",
                payload={"description": "test"},
            )


def test_notify_orchestrator_missing_required_field_raises(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "test-lead"}, clear=False):
        hn_server._store = EventStore(db_path=tmp_path / "r.sqlite")
        with pytest.raises((ValueError, Exception)):
            # STARTED requires 'description'
            hn_server.notify_orchestrator(type="STARTED", payload={})


def test_notify_orchestrator_started_stores_row(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    db = tmp_path / "r.sqlite"
    es = EventStore(db_path=db)
    hn_server._store = es
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "my-lead"}, clear=False):
        with patch.object(hn_server, "_post_to_listener", return_value=False):
            result = hn_server.notify_orchestrator(
                type="STARTED",
                payload={"description": "doing stuff"},
            )
    assert "stored_id" in result
    assert result["stored_id"] > 0
    assert result["delivered"] is False

    row = es.get_event(result["stored_id"])
    assert row is not None
    assert row["lead"] == "my-lead"
    assert row["type"] == "STARTED"


def test_notify_orchestrator_completed_stores_row(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    db = tmp_path / "r.sqlite"
    es = EventStore(db_path=db)
    hn_server._store = es
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "my-lead"}, clear=False):
        with patch.object(hn_server, "_post_to_listener", return_value=True):
            result = hn_server.notify_orchestrator(
                type="COMPLETED",
                payload={"summary": "all done", "paths": [], "urls": []},
            )
    assert result["delivered"] is True
    assert result["stored_id"] > 0


def test_notify_orchestrator_needs_input_creates_pending(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    db = tmp_path / "r.sqlite"
    es = EventStore(db_path=db)
    hn_server._store = es
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "q-lead"}, clear=False):
        with patch.object(hn_server, "_post_to_listener", return_value=False):
            result = hn_server.notify_orchestrator(
                type="NEEDS_INPUT",
                payload={
                    "question": "Which option?",
                    "options": ["a", "b"],
                    "timeout": 300,
                },
            )
    assert result["stored_id"] > 0
    pending = es.get_open_pending_inputs()
    assert len(pending) == 1
    assert pending[0]["question"] == "Which option?"


def test_notify_orchestrator_error_truncates_traceback(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    db = tmp_path / "r.sqlite"
    es = EventStore(db_path=db)
    hn_server._store = es
    long_tb = "x" * 6000
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "err-lead"}, clear=False):
        with patch.object(hn_server, "_post_to_listener", return_value=False):
            result = hn_server.notify_orchestrator(
                type="ERROR",
                payload={
                    "error": "something broke",
                    "context": "while doing x",
                    "traceback": long_tb,
                    "recoverable": False,
                },
            )
    row = es.get_event(result["stored_id"])
    p = json.loads(row["payload_json"])
    assert len(p["traceback"]) == 5003  # 5000 + "..."


def test_notify_orchestrator_milestone_percent_wrong_type_raises(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_notify import server as hn_server
    db = tmp_path / "r.sqlite"
    hn_server._store = EventStore(db_path=db)
    with patch.dict("os.environ", {"HERMES_LEAD_NAME": "l"}, clear=False):
        with pytest.raises((ValueError, Exception)):
            hn_server.notify_orchestrator(
                type="MILESTONE",
                payload={"progress": "doing stuff", "percent": "seventy-five"},
            )


# ------------------------------------------------------------------ HTTP listener integration tests

def test_http_listener_health_endpoint(tmp_path: Path) -> None:
    """Spin up the listener and verify /health returns 200."""
    import threading
    import http.server
    import urllib.request

    from claude_soma.mcp_servers.hermes_api import server as ha_server
    ha_server._store = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._milestone_last_dmed = {}

    port = _find_free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), ha_server._NotifyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            body = json.loads(resp.read())
        assert body["status"] == "ok"
    finally:
        srv.shutdown()


def test_http_listener_post_notify_queues_delivery(tmp_path: Path) -> None:
    import threading
    import http.server
    import urllib.request

    from claude_soma.mcp_servers.hermes_api import server as ha_server

    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es
    ha_server._milestone_last_dmed = {}

    # Pre-insert a row (as hermes-notify MCP tool would)
    eid = es.insert_event(
        lead="tl", type_="STARTED", ts=time.time(), payload_json='{"description":"x"}'
    )

    port = _find_free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), ha_server._NotifyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    delivered_calls = []

    def fake_deliver(event_id, lead, type_, payload_json):
        delivered_calls.append(event_id)
        es.mark_delivered(event_id)

    with patch.object(ha_server, "_deliver_event", side_effect=fake_deliver):
        body = json.dumps({
            "event_id": eid, "lead": "tl", "type": "STARTED",
            "payload_json": '{"description":"x"}'
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/notify",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
        assert resp.status == 202
        assert result["event_id"] == eid
        # Give the delivery thread a moment
        time.sleep(0.2)

    srv.shutdown()
    assert eid in delivered_calls


def test_http_listener_post_notify_invalid_json_returns_400(tmp_path: Path) -> None:
    import threading
    import http.server
    import urllib.request

    from claude_soma.mcp_servers.hermes_api import server as ha_server
    ha_server._store = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._milestone_last_dmed = {}

    port = _find_free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), ha_server._NotifyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/notify",
            data=b"not-json!!!",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        srv.shutdown()


def test_http_listener_post_notify_unknown_type_returns_400(tmp_path: Path) -> None:
    import threading
    import http.server
    import urllib.request

    from claude_soma.mcp_servers.hermes_api import server as ha_server
    ha_server._store = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._milestone_last_dmed = {}

    eid = ha_server._store.insert_event(
        lead="l", type_="STARTED", ts=time.time(), payload_json='{"description":"x"}'
    )
    port = _find_free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), ha_server._NotifyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        body = json.dumps({
            "event_id": eid, "lead": "l", "type": "BOGUS",
            "payload_json": '{"x": 1}'
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/notify",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        srv.shutdown()


def test_http_listener_milestone_throttle(tmp_path: Path) -> None:
    """Within-throttle-window MILESTONE should not re-DM."""
    from claude_soma.mcp_servers.hermes_api import server as ha_server

    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es
    ha_server._milestone_last_dmed = {"throttled-lead": time.time()}  # just DM'd
    ha_server._milestone_lock = __import__("threading").Lock()

    eid = es.insert_event(
        lead="throttled-lead",
        type_="MILESTONE",
        ts=time.time(),
        payload_json='{"progress": "step 2"}',
    )

    dm_calls = []
    with patch.object(ha_server, "_send_proactive_dm", side_effect=lambda *a, **kw: dm_calls.append(a)):
        ha_server._deliver_event(eid, "throttled-lead", "MILESTONE", '{"progress":"step 2"}')

    # DM should NOT fire within the throttle window
    assert len(dm_calls) == 0
    # Event should NOT be marked delivered (it's still pending for the next window)
    row = es.get_event(eid)
    assert row["delivered_at"] is None


def test_http_listener_milestone_fires_outside_throttle(tmp_path: Path) -> None:
    """Outside throttle window, MILESTONE DM fires."""
    from claude_soma.mcp_servers.hermes_api import server as ha_server

    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es
    ha_server._milestone_last_dmed = {"fresh-lead": time.time() - 400}  # > 5 min ago
    ha_server._milestone_lock = __import__("threading").Lock()

    eid = es.insert_event(
        lead="fresh-lead",
        type_="MILESTONE",
        ts=time.time(),
        payload_json='{"progress": "step fresh"}',
    )

    with patch.object(ha_server, "_send_proactive_dm", return_value=999):
        ha_server._deliver_event(eid, "fresh-lead", "MILESTONE", '{"progress":"step fresh"}')

    row = es.get_event(eid)
    assert row["delivered_at"] is not None


def test_drain_on_startup_redelivers_urgent(tmp_path: Path) -> None:
    """Drain should attempt delivery for undelivered COMPLETED/NEEDS_INPUT/ERROR rows."""
    from claude_soma.mcp_servers.hermes_api import server as ha_server

    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es
    ha_server._milestone_last_dmed = {}
    ha_server._milestone_lock = __import__("threading").Lock()

    eid = es.insert_event(
        lead="l", type_="COMPLETED", ts=time.time(), payload_json='{"summary":"done"}'
    )

    delivered = []
    with patch.object(ha_server, "_deliver_event", side_effect=lambda *a: delivered.append(a[0])):
        ha_server._drain_on_startup()

    assert eid in delivered


def test_resolve_pending_input_mcp_tool(tmp_path: Path) -> None:
    """resolve_pending_input MCP tool marks pending_inputs resolved."""
    from claude_soma.mcp_servers.hermes_api import server as ha_server

    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es

    ts = time.time()
    eid, _ = es.insert_event_with_pending_input(
        lead="l",
        ts=ts,
        payload_json='{"question":"yes or no?"}',
        question="yes or no?",
        options_json=None,
        timeout_secs=None,
    )

    result = ha_server.resolve_pending_input(eid, "yes")
    assert result["resolved"] is True
    assert len(es.get_open_pending_inputs()) == 0


def test_get_recent_lead_events_mcp_tool(tmp_path: Path) -> None:
    from claude_soma.mcp_servers.hermes_api import server as ha_server

    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es

    es.insert_event(lead="l1", type_="STARTED", ts=time.time(), payload_json='{"description":"x"}')
    es.insert_event(lead="l2", type_="ERROR", ts=time.time(), payload_json='{"error":"x","context":"y"}')

    all_events = ha_server.get_recent_lead_events()
    assert len(all_events) == 2

    l1_events = ha_server.get_recent_lead_events(lead="l1")
    assert len(l1_events) == 1
    assert l1_events[0]["lead"] == "l1"


# ------------------------------------------------------------------ DM-pipeline regression

# Regression for the 2026-05-31 bug: the _format_*_dm helpers produce Telegram
# HTML, but _send_proactive_dm was piping that output through gfm_to_html (the
# GFM-to-HTML converter intended for send_tg_reply's markdown inputs). gfm_to_html
# entity-escapes raw HTML chars outside known markers, so <b>/<code>/<a>/<pre>
# became &lt;b&gt;/&lt;code&gt;/&lt;a&gt;/&lt;pre&gt;, and Telegram (with
# parse_mode=HTML) rendered the entities as literal tag text. Fix: skip the
# converter, html-escape user fields in the formatters themselves.

def test_started_dm_keeps_html_tags_after_chunking() -> None:
    from claude_soma.mcp_servers.hermes_api.server import _format_started_dm
    from claude_soma.mcp_servers.hermes_api.tg_html import chunk_html_for_telegram
    text = _format_started_dm("hello-test", {"description": "hello world"})
    joined = "".join(chunk_html_for_telegram(text))
    assert "<b>" in joined and "</b>" in joined
    assert "<code>hello-test</code>" in joined
    assert "&lt;b&gt;" not in joined
    assert "&lt;code&gt;" not in joined


def test_completed_dm_escapes_user_html_but_keeps_template_tags() -> None:
    from claude_soma.mcp_servers.hermes_api.server import _format_completed_dm
    text, _ = _format_completed_dm("alpha", {"summary": "A <script>x</script> & B"})
    # template-side tags remain intact
    assert "<b>Lead <code>alpha</code> completed:</b>" in text
    # user-side HTML is entity-escaped (no injection through the summary field)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text and "&amp;" in text


def test_completed_dm_escapes_url_in_both_href_and_body() -> None:
    from claude_soma.mcp_servers.hermes_api.server import _format_completed_dm
    text, _ = _format_completed_dm(
        "alpha", {"summary": "s", "urls": ['https://x.test/?a=1&b="2"']},
    )
    # href attribute uses quote=True (escapes " → &quot;)
    assert 'href="https://x.test/?a=1&amp;b=&quot;2&quot;"' in text
    # body uses quote=False (& → &amp;, but " stays — also fine for body text)
    assert ">https://x.test/?a=1&amp;b=" in text


# ------------------------------------------------------------------ helpers

def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
