#!/usr/bin/env python3
"""Record a daily usage snapshot to /opt/claude-soma/usage.sqlite.

Runs at 23:55 IST via systemd timer. Calls `claude -p '/usage'` once.
Parses the JSON output and writes a row.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date


DB = os.environ.get("HERMES_USAGE_DB", "/opt/claude-soma/usage.sqlite")
CLAUDE = os.environ.get("HERMES_CLAUDE_BIN", "claude")


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_snapshots(
    date TEXT PRIMARY KEY,
    interactive_credits_used REAL DEFAULT 0,
    interactive_ceiling REAL DEFAULT 0,
    agent_sdk_credits_used REAL DEFAULT 0,
    agent_sdk_ceiling REAL DEFAULT 0,
    recorded_at REAL DEFAULT 0
);
"""


def _query_usage() -> dict:
    r = subprocess.run(
        [CLAUDE, "-p", "/usage", "--output-format", "json"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude -p /usage failed: {r.stderr[:500]}")
    last = r.stdout.strip().splitlines()[-1]
    return json.loads(last)


def _extract(payload: dict) -> tuple[float, float, float, float]:
    """Pull bucket numbers out of /usage JSON. Tolerant to schema variation."""
    interactive_used = float(payload.get("interactive_credits_used", 0) or 0)
    interactive_max = float(payload.get("interactive_credits_ceiling", 0) or 0)
    sdk_used = float(payload.get("agent_sdk_credits_used", 0) or 0)
    sdk_max = float(payload.get("agent_sdk_credits_ceiling", 0) or 0)
    return interactive_used, interactive_max, sdk_used, sdk_max


def main() -> int:
    try:
        payload = _query_usage()
    except Exception as e:  # noqa: BLE001
        print(f"usage_snapshot: query failed: {e}", file=sys.stderr)
        return 1

    iu, ic, su, sc = _extract(payload)
    conn = sqlite3.connect(DB, isolation_level=None)
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO daily_snapshots(date, interactive_credits_used,
            interactive_ceiling, agent_sdk_credits_used, agent_sdk_ceiling,
            recorded_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            interactive_credits_used=excluded.interactive_credits_used,
            interactive_ceiling=excluded.interactive_ceiling,
            agent_sdk_credits_used=excluded.agent_sdk_credits_used,
            agent_sdk_ceiling=excluded.agent_sdk_ceiling,
            recorded_at=excluded.recorded_at
        """,
        (date.today().isoformat(), iu, ic, su, sc, time.time()),
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
