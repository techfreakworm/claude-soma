from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from claude_soma.api.auth import require_authed_user


router = APIRouter(prefix="/usage", dependencies=[Depends(require_authed_user)])


def _db_path() -> str:
    return os.environ.get("HERMES_USAGE_DB", "/opt/claude-soma/usage.sqlite")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS daily_snapshots(
        date TEXT PRIMARY KEY,
        interactive_credits_used REAL DEFAULT 0,
        interactive_ceiling REAL DEFAULT 0,
        agent_sdk_credits_used REAL DEFAULT 0,
        agent_sdk_ceiling REAL DEFAULT 0,
        recorded_at REAL DEFAULT 0
    );
    """)


@router.get("")
def get_usage() -> dict:
    conn = None
    row = None
    trend: list[dict] = []
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        today = datetime.now(timezone.utc).date().isoformat()
        row = conn.execute(
            "SELECT * FROM daily_snapshots WHERE date = ?", (today,)
        ).fetchone()
        trend = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT 30"
        ).fetchall()]
    except sqlite3.Error:
        row, trend = None, []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def pct(used: float, ceiling: float) -> float:
        return 0.0 if ceiling <= 0 else round(100.0 * used / ceiling, 1)

    ic = row["interactive_ceiling"] if row else 0.0
    ac = row["agent_sdk_ceiling"] if row else 0.0
    interactive = {
        "today": row["interactive_credits_used"] if row else 0.0,
        "ceiling": ic,
        "remaining_pct": 100.0 - pct(
            row["interactive_credits_used"] if row else 0.0,
            ic,
        ),
        "configured": ic > 0,
    }
    agent_sdk = {
        "today": row["agent_sdk_credits_used"] if row else 0.0,
        "ceiling": ac,
        "remaining_pct": 100.0 - pct(
            row["agent_sdk_credits_used"] if row else 0.0,
            ac,
        ),
        "configured": ac > 0,
    }
    return {"interactive": interactive, "agent_sdk": agent_sdk, "trend": trend}
