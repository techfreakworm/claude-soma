from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from claude_soma.mcp_servers.hermes_api import server as ha_server


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
