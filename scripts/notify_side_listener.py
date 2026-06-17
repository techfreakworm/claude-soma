#!/usr/bin/env python3
"""Out-of-process proof harness for cross-host FI-NOTIFY (Step 7, N7b).

Runs the patched notify listener on a SIDE port (9101, NOT the live 9100) bound
to A's tailnet IP, so a remote B-lead's MILESTONE can be proven to reach the
operator's Discord WITHOUT restarting the channel. Throwaway: kill after the
proof. Events insert into A's shared registry.sqlite and drain normally later.

Usage (on A):
  export HERMES_NOTIFY_TOKEN=<shared bearer>   # same as B's secrets.env
  /opt/claude-soma/.venv/bin/python /opt/claude-soma/scripts/notify_side_listener.py
"""
import os
import threading

os.environ.setdefault("HERMES_NOTIFY_PORT", "9101")
os.environ.setdefault("HERMES_NOTIFY_BIND", "100.103.37.115")

from claude_soma.mcp_servers.hermes_api import server as s  # noqa: E402

s._store = s.EventStore()
s._milestone_last_dmed = {}
s._start_notify_listener()  # spawns per-bind serve_forever daemon threads
print(f"side listener up on {os.environ['HERMES_NOTIFY_BIND']}:{os.environ['HERMES_NOTIFY_PORT']}", flush=True)
threading.Event().wait()  # park forever (Ctrl-C / kill to stop)
