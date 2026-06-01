#!/usr/bin/env python3
"""Refresh stale Remote Control URLs for active project-leads.

Invoked daily by claude-soma-rc-url-refresh.timer (04:00 UTC), so the
04:30 UTC daily-status digest carries fresh URLs.

Behavior per active lead:
  1. Skip if is_lead_alive(name) is False (no tmux session) -- log "skipped:dead".
  2. Skip if pane shows lead is mid-thinking (heuristic: tail line ends with
     non-prompt content). Log "skipped:busy". Conservative -- false-positive
     skips a refresh that day; we get it tomorrow.
  3. Type /remote-control + Enter to the pane via tmux send-keys.
  4. Sleep briefly (~2s) for claude to print the menu.
  5. Capture pane, parse with spawner.RC_URL_RX to extract the URL.
  6. Type Enter to dismiss the menu / continue.
  7. If captured URL != registry value: UPDATE registry rc_url + last_activity.
     Log "refreshed:<name>:<old>->...<new-tail>".
  8. If captured URL == registry: log "noop:<name>". (No registry write --
     avoids spurious last_activity bumps that mislead the reaper / status
     digest.)
  9. On any exception within a single lead: log "error:<name>:<exc>" and
     continue to the next lead.

Log target: /var/log/claude-soma/rc-refresh.log (one JSON line per lead, plus
a final summary line).

Returns nothing; exit 0 on completion (even if some leads errored).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from claude_soma.mcp_servers.project_orchestrator.registry import Registry
from claude_soma.mcp_servers.project_orchestrator.spawner import (
    RC_URL_RX,
    LEAD_SOCKET_PREFIX,
    TMUX_SESSION_PREFIX,
    is_lead_alive,
)


DB = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
LOG_PATH = os.environ.get("HERMES_RC_REFRESH_LOG", "/var/log/claude-soma/rc-refresh.log")
TMUX_BIN = os.environ.get("HERMES_TMUX_BIN", "/usr/bin/tmux")

BUSY_PATTERNS = ("Bloviating…", "Crunched", "Worked for", "Wandering…", "Baked for")
IDLE_PROMPT = "❯ "
_IDLE_RE = re.compile(r"^\s*❯\s*$")

SEND_SLEEP = float(os.environ.get("HERMES_RC_REFRESH_SLEEP", "2"))


def _bare(agent_id: str) -> str:
    if agent_id.startswith(TMUX_SESSION_PREFIX):
        return agent_id[len(TMUX_SESSION_PREFIX):]
    return agent_id


def _socket(bare: str) -> str:
    return f"{LEAD_SOCKET_PREFIX}{bare}"


def _capture_pane(session: str, socket: str) -> str:
    result = subprocess.run(
        [TMUX_BIN, "-L", socket, "capture-pane", "-p", "-t", session],
        capture_output=True, text=True, check=True, timeout=10,
    )
    return result.stdout or ""


def _send_keys(session: str, socket: str, *args: str) -> None:
    subprocess.run(
        [TMUX_BIN, "-L", socket, "send-keys", "-t", session, *args],
        capture_output=True, text=True, check=True, timeout=10,
    )


def _is_busy(pane_text: str) -> bool:
    lines = [ln for ln in pane_text.splitlines() if ln.strip()]
    if not lines:
        print("rc-refresh: _is_busy: empty pane → treating as busy", file=sys.stderr)
        return True
    tail = lines[-2:] if len(lines) >= 2 else lines
    tail_joined = "\n".join(tail)
    for pat in BUSY_PATTERNS:
        if pat in tail_joined:
            print(f"rc-refresh: _is_busy: busy pattern {pat!r} → skip", file=sys.stderr)
            return True
    last = lines[-1]
    if _IDLE_RE.match(last):
        return False
    print(f"rc-refresh: _is_busy: ambiguous tail {repr(last)[:60]} → skip (conservative)", file=sys.stderr)
    return True


def _log(fh, data: dict) -> None:
    fh.write(json.dumps(data, separators=(",", ":")) + "\n")
    fh.flush()


def run_once(db_path: str | None = None, log_path: str | None = None) -> dict:
    db = db_path or os.environ.get("HERMES_ORCH_DB", DB)
    lp = log_path or os.environ.get("HERMES_RC_REFRESH_LOG", LOG_PATH)

    Path(lp).parent.mkdir(parents=True, exist_ok=True)

    refreshed = 0
    noop = 0
    skipped_dead = 0
    skipped_busy = 0
    errors = 0

    reg = Registry(db)
    try:
        with open(lp, "a") as fh:
            for p in reg.list_active():
                name = p["name"]
                agent_id = p["agent_id"]
                old_url = p.get("rc_url") or ""
                bare = _bare(agent_id)
                session = agent_id
                socket = _socket(bare)

                try:
                    if not is_lead_alive(agent_id):
                        _log(fh, {"ts": time.time(), "lead": name, "result": "skipped:dead"})
                        skipped_dead += 1
                        continue

                    pane = _capture_pane(session, socket)
                    if _is_busy(pane):
                        _log(fh, {"ts": time.time(), "lead": name, "result": "skipped:busy"})
                        skipped_busy += 1
                        continue

                    _send_keys(session, socket, "-l", "/remote-control")
                    _send_keys(session, socket, "Enter")
                    time.sleep(SEND_SLEEP)

                    pane_after = _capture_pane(session, socket)
                    _send_keys(session, socket, "Enter")

                    match = RC_URL_RX.search(pane_after)
                    if not match:
                        raise ValueError(
                            f"no RC URL found in pane (len={len(pane_after)})"
                        )

                    new_url = match.group(0)
                    if new_url == old_url:
                        _log(fh, {"ts": time.time(), "lead": name, "result": "noop"})
                        noop += 1
                    else:
                        with reg._lock:
                            reg._conn.execute(
                                "UPDATE projects SET rc_url = ?, last_activity = ?"
                                " WHERE name = ?",
                                (new_url, time.time(), name),
                            )
                        _log(fh, {
                            "ts": time.time(),
                            "lead": name,
                            "result": "refreshed",
                            "old_tail": old_url[-24:],
                            "new_tail": new_url[-24:],
                        })
                        refreshed += 1

                except Exception as exc:  # noqa: BLE001
                    _log(fh, {"ts": time.time(), "lead": name,
                              "result": "error", "detail": str(exc)})
                    errors += 1

            summary = {
                "ts": time.time(),
                "summary": {
                    "refreshed": refreshed,
                    "noop": noop,
                    "skipped_dead": skipped_dead,
                    "skipped_busy": skipped_busy,
                    "errors": errors,
                },
            }
            _log(fh, summary)
    finally:
        reg.close()

    return summary["summary"]


def main() -> None:
    counts = run_once()
    print(
        f"rc-url-refresh: refreshed={counts['refreshed']} noop={counts['noop']}"
        f" skipped_dead={counts['skipped_dead']} skipped_busy={counts['skipped_busy']}"
        f" errors={counts['errors']}"
    )


if __name__ == "__main__":
    main()
