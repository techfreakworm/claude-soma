from __future__ import annotations

import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_soma.mcp_servers.hermes_api import server as ha_server
from claude_soma.mcp_servers.hermes_api.notify_store import EventStore


def test_so_reuseaddr_set_before_bind() -> None:
    """SO_REUSEADDR must be setsockopt'd on the socket before server_bind is called."""
    call_order: list[tuple] = []

    mock_sock = MagicMock()
    mock_sock.setsockopt.side_effect = lambda *a: call_order.append(("setsockopt", a))

    mock_server = MagicMock()
    mock_server.socket = mock_sock
    mock_server.server_bind.side_effect = lambda: call_order.append(("server_bind",))
    mock_server.server_activate.return_value = None
    mock_server.serve_forever.side_effect = SystemExit(0)

    with patch("http.server.ThreadingHTTPServer", return_value=mock_server):
        try:
            ha_server._start_notify_listener()
        except SystemExit:
            pass

    ops = [op[0] for op in call_order]

    assert "setsockopt" in ops, "setsockopt was never called on the listener socket"
    assert "server_bind" in ops, "server_bind was never called"

    idx_setsockopt = ops.index("setsockopt")
    idx_bind = ops.index("server_bind")
    assert idx_setsockopt < idx_bind, (
        f"SO_REUSEADDR setsockopt (position {idx_setsockopt}) must precede "
        f"server_bind (position {idx_bind})"
    )

    setsockopt_args = call_order[idx_setsockopt][1]
    assert setsockopt_args[1] == socket.SO_REUSEADDR, (
        f"Expected SO_REUSEADDR ({socket.SO_REUSEADDR}), got {setsockopt_args[1]}"
    )


# ---- _maybe_trigger_auto_restart unit tests ---------------------------------

_VALID_RESTART_PAYLOAD = '{"progress": "RESTART REQUIRED deploy done (services: svc1,svc2)"}'
_NO_RESTART_PAYLOAD = '{"progress": "50% done"}'
_RESTART_NO_SERVICES_PAYLOAD = '{"progress": "RESTART REQUIRED — something happened"}'


def _make_store(tmp_path: Path) -> EventStore:
    es = EventStore(db_path=tmp_path / "r.sqlite")
    ha_server._store = es
    return es


def test_maybe_trigger_skips_non_milestone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """type_ != 'MILESTONE' must result in no Popen call."""
    _make_store(tmp_path)
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    with patch.object(ha_server.subprocess, "Popen") as mock_popen:
        ha_server._maybe_trigger_auto_restart(1, "l", "STARTED", '{"description":"x"}')

    mock_popen.assert_not_called()


def test_maybe_trigger_skips_no_restart_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MILESTONE without 'RESTART REQUIRED' in progress must not spawn Popen."""
    es = _make_store(tmp_path)
    eid = es.insert_event(lead="l", type_="MILESTONE", ts=time.time(), payload_json=_NO_RESTART_PAYLOAD)
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    with patch.object(ha_server.subprocess, "Popen") as mock_popen:
        ha_server._maybe_trigger_auto_restart(eid, "l", "MILESTONE", _NO_RESTART_PAYLOAD)

    mock_popen.assert_not_called()


def test_maybe_trigger_skips_no_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MILESTONE with 'RESTART REQUIRED' but no 'services:' clause must log and not spawn."""
    es = _make_store(tmp_path)
    eid = es.insert_event(lead="l", type_="MILESTONE", ts=time.time(), payload_json=_RESTART_NO_SERVICES_PAYLOAD)
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    logged: list[str] = []
    with patch.object(ha_server, "_log_notify_error", side_effect=logged.append):
        with patch.object(ha_server.subprocess, "Popen") as mock_popen:
            ha_server._maybe_trigger_auto_restart(eid, "l", "MILESTONE", _RESTART_NO_SERVICES_PAYLOAD)

    mock_popen.assert_not_called()
    assert any("services" in msg for msg in logged)


def test_maybe_trigger_skips_window_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When HERMES_AUTO_RESTART_WINDOW_UTC is unset, Popen must not be called."""
    es = _make_store(tmp_path)
    eid = es.insert_event(lead="l", type_="MILESTONE", ts=time.time(), payload_json=_VALID_RESTART_PAYLOAD)
    monkeypatch.delenv("HERMES_AUTO_RESTART_WINDOW_UTC", raising=False)

    with patch.object(ha_server.subprocess, "Popen") as mock_popen:
        ha_server._maybe_trigger_auto_restart(eid, "l", "MILESTONE", _VALID_RESTART_PAYLOAD)

    mock_popen.assert_not_called()


def test_maybe_trigger_fires_when_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid env + valid MILESTONE payload + first claim → Popen called with correct argv."""
    es = _make_store(tmp_path)
    eid = es.insert_event(lead="l", type_="MILESTONE", ts=time.time(), payload_json=_VALID_RESTART_PAYLOAD)
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    with patch.object(ha_server.subprocess, "Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        ha_server._maybe_trigger_auto_restart(eid, "l", "MILESTONE", _VALID_RESTART_PAYLOAD)

    mock_popen.assert_called_once()
    call_args = mock_popen.call_args
    argv = call_args[0][0]
    assert argv == [
        "setsid", "nohup", "sudo", "bash",
        ha_server._AUTO_RESTART_SCRIPT,
        "svc1,svc2",
    ]
    assert call_args.kwargs.get("start_new_session") is True


def test_maybe_trigger_skips_when_claim_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When claim_auto_restart returns False (already fired), Popen must not be called."""
    es = _make_store(tmp_path)
    eid = es.insert_event(lead="l", type_="MILESTONE", ts=time.time(), payload_json=_VALID_RESTART_PAYLOAD)
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    # Claim the row first so a second claim returns False
    claimed = es.claim_auto_restart(eid)
    assert claimed is True

    with patch.object(ha_server.subprocess, "Popen") as mock_popen:
        ha_server._maybe_trigger_auto_restart(eid, "l", "MILESTONE", _VALID_RESTART_PAYLOAD)

    mock_popen.assert_not_called()


# ---- cluster C: _maybe_trigger_automation listener end-to-end tests ---------

def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_handle_notify_restart_milestone_fires_dispatch_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /notify with RESTART REQUIRED MILESTONE: 202 + action_fired_at set + Popen called."""
    import http.server
    import json
    import threading
    import urllib.request
    from unittest.mock import MagicMock

    es = _make_store(tmp_path)
    ha_server._milestone_last_dmed = {}

    eid = es.insert_event(
        lead="l", type_="MILESTONE", ts=time.time(),
        payload_json=_VALID_RESTART_PAYLOAD,
    )
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    port = _find_free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), ha_server._NotifyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        with patch.object(ha_server.subprocess, "Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            with patch.object(ha_server, "_deliver_event"):
                body = json.dumps({
                    "event_id": eid, "lead": "l", "type": "MILESTONE",
                    "payload_json": _VALID_RESTART_PAYLOAD,
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

        row = es.get_event(eid)
        assert row["action_fired_at"] is not None
        assert row["action_key"] == "restart"
        mock_popen.assert_called_once()
    finally:
        srv.shutdown()


def test_duplicate_restart_milestones_each_fire_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two POSTs for same event_id: claim wins on first only → Popen called exactly once."""
    import http.server
    import json
    import threading
    import urllib.request
    from unittest.mock import MagicMock

    es = _make_store(tmp_path)
    ha_server._milestone_last_dmed = {}

    eid = es.insert_event(
        lead="l", type_="MILESTONE", ts=time.time(),
        payload_json=_VALID_RESTART_PAYLOAD,
    )
    monkeypatch.setenv("HERMES_AUTO_RESTART_WINDOW_UTC", str(int(time.time()) + 300))

    port = _find_free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), ha_server._NotifyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    popen_calls: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        popen_calls.append(argv)
        return MagicMock()

    try:
        with patch.object(ha_server.subprocess, "Popen", side_effect=fake_popen):
            with patch.object(ha_server, "_deliver_event"):
                body = json.dumps({
                    "event_id": eid, "lead": "l", "type": "MILESTONE",
                    "payload_json": _VALID_RESTART_PAYLOAD,
                }).encode()
                for _ in range(2):
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/notify",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5):
                        pass

        assert len(popen_calls) == 1, (
            f"Expected exactly one Popen call, got {len(popen_calls)}"
        )
    finally:
        srv.shutdown()
