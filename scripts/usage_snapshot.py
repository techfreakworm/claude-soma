#!/usr/bin/env python3
"""Record a daily usage snapshot to /opt/claude-soma/usage.sqlite.

Runs at 23:55 IST via systemd timer. Scans local JSONL transcripts under
~/.claude/projects/*/*.jsonl; no claude subprocess is spawned.

For each assistant message recorded today (UTC), sums
input_tokens + output_tokens + cache_creation_input_tokens and buckets
by service_tier: "batch"/"priority" → agent_sdk; everything else → interactive.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any


DB = os.environ.get("HERMES_USAGE_DB", "/opt/claude-soma/usage.sqlite")


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


def _to_f(val: Any) -> float:
    """Safely convert value to float, defaulting to 0.0."""
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _query_usage() -> dict:
    today = date.today().isoformat()
    projects_root = Path.home() / ".claude" / "projects"
    iu = 0.0
    au = 0.0

    if not projects_root.exists():
        pass
    else:
        for jsonl_path in projects_root.glob("*/*.jsonl"):
            try:
                with jsonl_path.open(encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        ts = obj.get("timestamp", "")
                        if not isinstance(ts, str) or not ts.startswith(today):
                            continue

                        msg = obj.get("message")
                        if not isinstance(msg, dict):
                            continue

                        usage = msg.get("usage")
                        if not isinstance(usage, dict):
                            continue

                        tokens = (
                            _to_f(usage.get("input_tokens"))
                            + _to_f(usage.get("output_tokens"))
                            + _to_f(usage.get("cache_creation_input_tokens"))
                        )

                        tier = usage.get("service_tier", "")
                        if tier in ("batch", "priority"):
                            au += tokens
                        else:
                            iu += tokens
            except OSError:
                continue

    return {
        "interactive_credits_used": iu,
        "interactive_credits_ceiling": _to_f(os.environ.get("HERMES_INTERACTIVE_CEILING")),
        "agent_sdk_credits_used": au,
        "agent_sdk_credits_ceiling": _to_f(os.environ.get("HERMES_AGENT_SDK_CEILING")),
    }


def _extract(payload: dict) -> tuple[float, float, float, float]:
    """Pull bucket numbers out of usage dict. Tolerant to schema variation."""
    iu = _to_f(payload.get("interactive_credits_used"))
    ic = _to_f(payload.get("interactive_credits_ceiling"))
    su = _to_f(payload.get("agent_sdk_credits_used"))
    sc = _to_f(payload.get("agent_sdk_credits_ceiling"))
    return iu, ic, su, sc


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
