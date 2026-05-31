from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_DB_DEFAULT = "/opt/claude-soma/registry.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lead_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lead             TEXT    NOT NULL,
    type             TEXT    NOT NULL
                             CHECK (type IN ('STARTED','MILESTONE','COMPLETED','NEEDS_INPUT','ERROR')),
    ts               REAL    NOT NULL,
    payload_json     TEXT    NOT NULL,
    created_at       REAL    NOT NULL,
    delivered_at     REAL,
    delivery_error   TEXT,
    hook_injected_at REAL
);

CREATE INDEX IF NOT EXISTS idx_le_lead
    ON lead_events (lead);
CREATE INDEX IF NOT EXISTS idx_le_type
    ON lead_events (type);
CREATE INDEX IF NOT EXISTS idx_le_undelivered
    ON lead_events (delivered_at)
    WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_le_uninjected
    ON lead_events (hook_injected_at)
    WHERE hook_injected_at IS NULL;

CREATE TABLE IF NOT EXISTS pending_inputs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES lead_events(id),
    lead         TEXT    NOT NULL,
    question     TEXT    NOT NULL,
    options_json TEXT,
    timeout_secs INTEGER,
    tg_msg_id    INTEGER,
    status       TEXT    NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','resolved','timed_out')),
    created_at   REAL    NOT NULL,
    resolved_at  REAL,
    answer       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pi_open
    ON pending_inputs (status)
    WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_pi_lead
    ON pending_inputs (lead);
"""

VALID_TYPES = frozenset({"STARTED", "MILESTONE", "COMPLETED", "NEEDS_INPUT", "ERROR"})
URGENT_TYPES = frozenset({"COMPLETED", "NEEDS_INPUT", "ERROR"})


class EventStore:
    """Thread-safe SQLite store for lead lifecycle events.

    Follows the same lock+single-connection pattern as Registry to avoid
    'database is locked' contention from multiple connections. Uses
    isolation_level=None (autocommit) for simplicity; explicit BEGIN/COMMIT
    is used for the rare multi-statement atomic operations.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = os.environ.get("HERMES_ORCH_DB", _DB_DEFAULT)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ writes

    def insert_event(
        self,
        *,
        lead: str,
        type_: str,
        ts: float,
        payload_json: str,
    ) -> int:
        """Insert a new lead_events row. Returns the new row id."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO lead_events
                    (lead, type, ts, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lead, type_, ts, payload_json, now),
            )
        return cur.lastrowid  # type: ignore[return-value]

    def insert_event_with_pending_input(
        self,
        *,
        lead: str,
        ts: float,
        payload_json: str,
        question: str,
        options_json: str | None,
        timeout_secs: int | None,
    ) -> tuple[int, int]:
        """Atomically insert a NEEDS_INPUT lead_events row + pending_inputs row.
        Returns (event_id, pending_input_id)."""
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur_ev = self._conn.execute(
                    """
                    INSERT INTO lead_events
                        (lead, type, ts, payload_json, created_at)
                    VALUES (?, 'NEEDS_INPUT', ?, ?, ?)
                    """,
                    (lead, ts, payload_json, now),
                )
                event_id = cur_ev.lastrowid
                cur_pi = self._conn.execute(
                    """
                    INSERT INTO pending_inputs
                        (event_id, lead, question, options_json, timeout_secs, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, lead, question, options_json, timeout_secs, now),
                )
                pending_id = cur_pi.lastrowid
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return event_id, pending_id  # type: ignore[return-value]

    def mark_delivered(self, event_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE lead_events SET delivered_at = ?, delivery_error = NULL WHERE id = ?",
                (time.time(), event_id),
            )

    def mark_delivery_error(self, event_id: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE lead_events SET delivery_error = ? WHERE id = ?",
                (error[:500], event_id),
            )

    def mark_tg_msg_id(self, pending_input_id: int, tg_msg_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pending_inputs SET tg_msg_id = ? WHERE id = ?",
                (tg_msg_id, pending_input_id),
            )

    def mark_pending_resolved(self, event_id: int, answer: str) -> bool:
        """Mark the open pending_input for event_id as resolved. Returns True if found."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE pending_inputs
                   SET status = 'resolved', resolved_at = ?, answer = ?
                 WHERE event_id = ? AND status = 'open'
                """,
                (now, answer, event_id),
            )
        return (cur.rowcount or 0) > 0

    def mark_pending_timed_out(self, event_id: int) -> bool:
        """Mark the open pending_input for event_id as timed_out. Returns True if found."""
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE pending_inputs
                   SET status = 'timed_out', resolved_at = ?
                 WHERE event_id = ? AND status = 'open'
                """,
                (time.time(), event_id),
            )
        return (cur.rowcount or 0) > 0

    def mark_hook_injected(self, event_ids: list[int]) -> None:
        """Mark a batch of events as hook-injected at now."""
        if not event_ids:
            return
        now = time.time()
        placeholders = ",".join("?" * len(event_ids))
        with self._lock:
            self._conn.execute(
                f"UPDATE lead_events SET hook_injected_at = ? WHERE id IN ({placeholders})",
                [now, *event_ids],
            )

    # ------------------------------------------------------------------ reads

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM lead_events WHERE id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_undelivered_urgent(self) -> list[dict[str, Any]]:
        """Return rows with delivered_at IS NULL for COMPLETED/NEEDS_INPUT/ERROR."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM lead_events
                 WHERE delivered_at IS NULL
                   AND type IN ('COMPLETED','NEEDS_INPUT','ERROR')
                 ORDER BY id ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get_uninjected(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return events not yet injected by the hook, newest-first."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM lead_events
                 WHERE hook_injected_at IS NULL
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent(
        self,
        lead: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if lead:
                rows = self._conn.execute(
                    "SELECT * FROM lead_events WHERE lead = ? ORDER BY id DESC LIMIT ?",
                    (lead, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM lead_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_open_pending_inputs(self, limit: int = 1) -> list[dict[str, Any]]:
        """Return oldest open NEEDS_INPUT questions (FIFO, oldest first)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM pending_inputs
                 WHERE status = 'open'
                 ORDER BY id ASC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            raw = d.get("options_json")
            d["options"] = json.loads(raw) if raw else []
            result.append(d)
        return result

    def get_milestone_last_delivered_times(self) -> dict[str, float]:
        """Return {lead: last_milestone_delivered_ts} for MILESTONE throttle init."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT lead, MAX(delivered_at) as last_dm
                  FROM lead_events
                 WHERE type = 'MILESTONE' AND delivered_at IS NOT NULL
                 GROUP BY lead
                """
            ).fetchall()
        return {r["lead"]: float(r["last_dm"]) for r in rows if r["last_dm"]}

    def get_undelivered_milestones(self, lead: str) -> list[dict[str, Any]]:
        """Return undelivered MILESTONE rows for a lead (for batch flush on COMPLETED)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM lead_events
                 WHERE lead = ? AND type = 'MILESTONE' AND delivered_at IS NULL
                 ORDER BY id ASC
                """,
                (lead,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
