# src/claude_soma/mcp_servers/project_orchestrator/registry.py
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    name           TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    type           TEXT NOT NULL,
    cwd            TEXT NOT NULL,
    rc_url         TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    permission_mode TEXT NOT NULL DEFAULT 'acceptEdits',
    spawned_at     REAL NOT NULL,
    last_activity  REAL NOT NULL,
    brief          TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_last_activity ON projects(last_activity);
"""


class Registry:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def register(
        self,
        name: str,
        *,
        agent_id: str,
        type_: str,
        cwd: str,
        rc_url: str | None,
        permission_mode: str = "acceptEdits",
        brief: str | None = None,
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO projects(name, agent_id, type, cwd, rc_url, status,
                                 permission_mode, spawned_at, last_activity, brief)
            VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                agent_id=excluded.agent_id, type=excluded.type, cwd=excluded.cwd,
                rc_url=excluded.rc_url, status='active',
                permission_mode=excluded.permission_mode,
                last_activity=excluded.last_activity, brief=excluded.brief
            """,
            (name, agent_id, type_, cwd, rc_url, permission_mode, now, now, brief),
        )

    def get(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_active(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM projects WHERE status = 'active' ORDER BY last_activity DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM projects ORDER BY last_activity DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, name: str, status: str) -> None:
        self._conn.execute(
            "UPDATE projects SET status = ?, last_activity = ? WHERE name = ?",
            (status, time.time(), name),
        )

    def touch(self, name: str) -> None:
        self._conn.execute(
            "UPDATE projects SET last_activity = ? WHERE name = ?",
            (time.time(), name),
        )

    def idle_for(self, name: str) -> float:
        row = self._conn.execute(
            "SELECT last_activity FROM projects WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return 0.0
        return max(0.0, time.time() - float(row["last_activity"]))

    def delete(self, name: str) -> None:
        self._conn.execute("DELETE FROM projects WHERE name = ?", (name,))

    def close(self) -> None:
        self._conn.close()
