#!/usr/bin/env python3
"""One-shot: backfill the 5 known routines (4 system + portfolio-oneliner) into
the registry so the live `/api/routines` endpoint returns canonical entries.
Idempotent — re-running upserts.

Usage on VPS: /opt/claude-soma/.venv/bin/python scripts/backfill_routines.py
"""
from __future__ import annotations

import os

from claude_soma.mcp_servers.project_orchestrator.registry import Registry


DEFAULTS = [
    ("healthcheck",        "system", "every 10 min",                 "healthcheck",        "Restart api/frontend/channel if any is down"),  # noqa: E501
    ("cache-refresh",      "system", "every 5 min",                  "cache-refresh",      "Prime hot dashboard API paths"),  # noqa: E501
    ("usage-snapshot",     "system", "every 15 min",                 "usage-snapshot",     "Every-15-min Max-credit usage snapshot"),  # noqa: E501
    ("idle-reaper",        "system", "every 6h",                     "idle-reaper",        "Hibernate idle project-leads >24h"),  # noqa: E501
    ("portfolio-oneliner", "bot",    "Mon..Fri *-*-* 03:30:00",      "portfolio-oneliner", "Weekday 09:00 IST portfolio brief"),  # noqa: E501
]


def main() -> int:
    db = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
    reg = Registry(db)
    try:
        for name, created_by, schedule, target_skill, description in DEFAULTS:
            reg.register_routine(
                name, kind="local", schedule=schedule,
                target_skill=target_skill, description=description,
                created_by=created_by,
            )
        listed = reg.list_routines()
        print(f"backfilled {len(listed)} routines:")
        for r in listed:
            print(f"  {r['name']:24s} {r['kind']:8s} {r['created_by']:8s} {r['schedule']}")
    finally:
        reg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
