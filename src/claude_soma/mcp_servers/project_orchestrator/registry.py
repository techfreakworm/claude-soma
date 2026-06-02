# src/claude_soma/mcp_servers/project_orchestrator/registry.py
from __future__ import annotations

import json
import sqlite3
import threading
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
    brief          TEXT,
    session_uuid   TEXT DEFAULT NULL,
    turn_count     INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS team_members (
    lead_name       TEXT NOT NULL,
    teammate_handle TEXT NOT NULL,
    role            TEXT NOT NULL,
    brief           TEXT NOT NULL,
    dispatched_at   REAL NOT NULL,
    last_seen_at    REAL,
    PRIMARY KEY (lead_name, teammate_handle)
);

CREATE INDEX IF NOT EXISTS idx_team_members_lead ON team_members(lead_name);
"""


class Registry:
    # One Registry instance is a long-lived singleton shared across threads:
    # the orchestrator MCP server uses it, and the FastAPI dashboard runs sync
    # route handlers in a threadpool, so .get()/.list_*() get called from worker
    # threads other than the one that opened the connection. sqlite forbids using
    # a connection across threads by default, which 500'd /api/projects/{name}/team.
    # We open with check_same_thread=False and serialize every connection access
    # behind a single lock -- a lone connection + lock also avoids the
    # "database is locked" contention multiple connections can hit.
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Backward-compat migration: add session_uuid column to existing DBs.
            # On a fresh DB the column is already in the SCHEMA above, so this
            # raises OperationalError("duplicate column name") and we silently
            # skip. On an existing DB that pre-dates this column, it succeeds.
            try:
                self._conn.execute(
                    "ALTER TABLE projects ADD COLUMN session_uuid TEXT DEFAULT NULL"
                )
            except sqlite3.OperationalError:
                pass
            # Backward-compat migration: add turn_count column to existing DBs.
            try:
                self._conn.execute(
                    "ALTER TABLE projects ADD COLUMN turn_count INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass

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
        with self._lock:
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
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE name = ?", (name,)
            ).fetchone()
        return dict(row) if row else None

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE status = 'active' ORDER BY last_activity DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY last_activity DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, name: str, status: str, *, bump_activity: bool = True) -> None:
        # bump_activity=False is for liveness reconciliation: flipping a vanished
        # lead to 'dead' is bookkeeping, not activity, so it must not reset the
        # idle clock (which would make a long-dead lead look freshly active).
        with self._lock:
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
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET last_activity = ? WHERE name = ?",
                (time.time(), name),
            )

    def idle_for(self, name: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_activity FROM projects WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return 0.0
        return max(0.0, time.time() - float(row["last_activity"]))

    def get_session_uuid(self, name: str) -> str | None:
        """Return the cloud session UUID for a project, or None if not set."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_uuid FROM projects WHERE name = ?", (name,)
            ).fetchone()
        return row["session_uuid"] if row else None

    def set_session_uuid(self, name: str, session_uuid: str) -> None:
        """Store the cloud session UUID so a dead lead can be resumed later."""
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET session_uuid = ? WHERE name = ?",
                (session_uuid, name),
            )

    def increment_turn_count(self, name: str) -> int:
        """Atomically increment turn_count for a lead; return the new value."""
        with self._lock:
            row = self._conn.execute(
                "UPDATE projects SET turn_count = turn_count + 1 "
                "WHERE name = ? RETURNING turn_count",
                (name,),
            ).fetchone()
        return row["turn_count"] if row else 0

    def get_turn_count(self, name: str) -> int:
        """Return the turn_count for a lead, or 0 if the lead does not exist."""
        with self._lock:
            row = self._conn.execute(
                "SELECT turn_count FROM projects WHERE name = ?",
                (name,),
            ).fetchone()
        return row["turn_count"] if row else 0

    def reset_turn_count(self, name: str) -> None:
        """Reset turn_count to 0 for a lead."""
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET turn_count = 0 WHERE name = ?",
                (name,),
            )

    def delete(self, name: str) -> None:
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routines ORDER BY name ASC"
            ).fetchall()
        return [self._row_to_routine(r) for r in rows]

    def get_routine(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM routines WHERE name = ?", (name,)
            ).fetchone()
        return self._row_to_routine(row) if row else None

    def delete_routine(self, name: str) -> None:
        with self._lock:
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
        with self._lock:
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

    def upsert_team_member(
        self,
        lead_name: str,
        teammate_handle: str,
        role: str,
        brief: str,
    ) -> None:
        """Insert or update a team member row.

        dispatched_at is set on first insert and preserved on subsequent upserts
        (only role, brief, and last_seen_at are refreshed).
        """
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO team_members(lead_name, teammate_handle, role, brief,
                                         dispatched_at, last_seen_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(lead_name, teammate_handle) DO UPDATE SET
                    role=excluded.role,
                    brief=excluded.brief,
                    last_seen_at=excluded.last_seen_at
                """,
                (lead_name, teammate_handle, role, brief, now, now),
            )

    def get_team_members(self, lead_name: str) -> list[dict[str, Any]]:
        """Return all persisted team members for a lead, ordered by handle."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT lead_name, teammate_handle, role, brief,
                       dispatched_at, last_seen_at
                FROM team_members WHERE lead_name = ?
                ORDER BY teammate_handle ASC
                """,
                (lead_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
