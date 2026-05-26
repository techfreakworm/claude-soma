# src/claude_soma/mcp_servers/project_orchestrator/registry.py
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


ALLOWED_ROUTINE_KINDS = frozenset({"cloud", "local"})
ALLOWED_ROUTINE_CREATORS = frozenset({"user", "bot", "system"})


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

CREATE TABLE IF NOT EXISTS routines (
    name            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('cloud', 'local')),
    schedule        TEXT NOT NULL,
    target_skill    TEXT,
    description     TEXT,
    last_run        REAL,
    next_run        REAL,
    created_by      TEXT NOT NULL DEFAULT 'bot'
                    CHECK (created_by IN ('user', 'bot', 'system')),
    created_at      REAL NOT NULL,
    metadata        TEXT
);

CREATE INDEX IF NOT EXISTS idx_routines_kind ON routines(kind);
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

    def set_status(self, name: str, status: str, *, bump_activity: bool = True) -> None:
        # bump_activity=False is for liveness reconciliation: flipping a vanished
        # lead to 'dead' is bookkeeping, not activity, so it must not reset the
        # idle clock (which would make a long-dead lead look freshly active).
        if bump_activity:
            self._conn.execute(
                "UPDATE projects SET status = ?, last_activity = ? WHERE name = ?",
                (status, time.time(), name),
            )
        else:
            self._conn.execute(
                "UPDATE projects SET status = ? WHERE name = ?",
                (status, name),
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

    def register_routine(
        self,
        name: str,
        *,
        kind: str,
        schedule: str,
        target_skill: str | None = None,
        description: str | None = None,
        created_by: str = "bot",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if kind not in ALLOWED_ROUTINE_KINDS:
            raise ValueError(
                f"invalid routine kind {kind!r}; expected one of "
                f"{sorted(ALLOWED_ROUTINE_KINDS)}"
            )
        if created_by not in ALLOWED_ROUTINE_CREATORS:
            raise ValueError(
                f"invalid created_by {created_by!r}; expected one of "
                f"{sorted(ALLOWED_ROUTINE_CREATORS)}"
            )
        now = time.time()
        meta_json = json.dumps(metadata) if metadata is not None else None
        self._conn.execute(
            """
            INSERT INTO routines(name, kind, schedule, target_skill, description,
                                 last_run, next_run, created_by, created_at, metadata)
            VALUES(?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                kind=excluded.kind,
                schedule=excluded.schedule,
                target_skill=excluded.target_skill,
                description=excluded.description,
                created_by=excluded.created_by,
                metadata=excluded.metadata
            """,
            (
                name, kind, schedule, target_skill, description,
                created_by, now, meta_json,
            ),
        )

    def list_routines(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM routines ORDER BY name ASC"
        ).fetchall()
        return [self._row_to_routine(r) for r in rows]

    def get_routine(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM routines WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_routine(row) if row else None

    def delete_routine(self, name: str) -> None:
        self._conn.execute("DELETE FROM routines WHERE name = ?", (name,))

    def update_routine_run(
        self,
        name: str,
        *,
        last_run: float | None = None,
        next_run: float | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if last_run is not None:
            sets.append("last_run = ?")
            params.append(last_run)
        if next_run is not None:
            sets.append("next_run = ?")
            params.append(next_run)
        if not sets:
            return
        params.append(name)
        self._conn.execute(
            f"UPDATE routines SET {', '.join(sets)} WHERE name = ?",
            params,
        )

    @staticmethod
    def _row_to_routine(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        raw = d.get("metadata")
        if raw:
            try:
                d["metadata"] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                d["metadata"] = None
        else:
            d["metadata"] = None
        return d

    def close(self) -> None:
        self._conn.close()
