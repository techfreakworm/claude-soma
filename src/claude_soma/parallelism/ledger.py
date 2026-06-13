"""Durable batch-task ledger for Phase-1 lead parallelism.

Stores one table -- batch_tasks -- in the shared registry.sqlite
(HERMES_ORCH_DB).  That file is already opened by the orchestrator
(registry.py) and the hermes-api (notify_store.py).  This module is the
THIRD logical tenant of the same file; it opens its own short-lived
connections rather than sharing a singleton.

Cross-process write safety (three-process boundary)
----------------------------------------------------
The orchestrator's threading.Lock (registry.py:77) provides NO protection
for writes originating in the lead process.  SQLite's own file lock plus
WAL mode is the actual serialization mechanism here.

  - journal_mode=WAL  : allows concurrent readers while a writer holds the
    WAL write lock; eliminates SQLITE_BUSY between readers and a single
    writer.  Persistent once set -- subsequent connections inherit it.

  - busy_timeout=5000 : when two writers race (two parallel leads both
    inserting/updating rows), SQLite retries for up to 5 s before raising
    OperationalError.  Enough headroom for all Phase-1 workloads.

  - isolation_level=None : autocommit.  Every single SQL statement is its
    own implicit transaction.  This means a single UPDATE statement is
    fully atomic -- the WHERE clause (including sub-selects) and the row
    mutation are serialized under one SQLite write lock.

Single-writer-per-row contract
-------------------------------
Each batch_tasks row is written exclusively by the lead that created the
batch (identified by lead_name).  No two leads write the *same* row.
This makes last-writer-wins per row acceptable and safe: concurrent leads
updating *different* rows cannot corrupt each other's state even if their
writes interleave.

Atomic cap-claim
----------------
claim_slot() uses a single UPDATE with a correlated sub-select:

    UPDATE batch_tasks
       SET status='running', ...
     WHERE batch_id=? AND task_id=? AND status='pending'
       AND (SELECT COUNT(*) FROM batch_tasks AS b2
             WHERE b2.lead_name = (SELECT lead_name FROM batch_tasks
                                    WHERE batch_id=? AND task_id=?)
               AND b2.status='running') < ?

SQLite executes this as one atomic write.  If the sub-select count
reaches the cap, the WHERE clause is false, rowcount == 0, and
claim_slot() returns False (backpressure).  No explicit transaction
or flock is required for Phase 1 (no leases).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import time
import uuid
from collections.abc import Generator
from typing import Any

_DEFAULT_DB: str = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_tasks (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id         TEXT    NOT NULL,
  lead_name        TEXT    NOT NULL,
  request_text     TEXT    NOT NULL DEFAULT '',
  task_id          TEXT    NOT NULL,
  contention_class TEXT    NOT NULL DEFAULT 'FREE',
  brief            TEXT    NOT NULL,
  worker_agent_id  TEXT,
  status           TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','running','done','failed','skipped')),
  result_summary   TEXT,
  error_msg        TEXT,
  created_at       REAL    NOT NULL,
  started_at       REAL,
  completed_at     REAL,
  UNIQUE(batch_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_bt_batch
    ON batch_tasks (batch_id);
CREATE INDEX IF NOT EXISTS idx_bt_lead_status
    ON batch_tasks (lead_name, status);
"""


# ---------------------------------------------------------------------------
# Internal connection helper
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _connect(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Open a short-lived connection with WAL mode and busy-timeout."""
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL allows concurrent readers while a writer holds the write lock.
    # Setting it is idempotent once WAL is active on the file.
    conn.execute("PRAGMA journal_mode=WAL")
    # Retry for up to 5 s on write contention before raising OperationalError.
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: str = _DEFAULT_DB) -> None:
    """Create the batch_tasks table if it does not exist (idempotent).

    Safe to call from multiple processes simultaneously: SQLite serializes
    DDL under its own write lock, and IF NOT EXISTS makes it a no-op on
    any connection that arrives after the first.
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def create_batch(
    lead_name: str,
    request_text: str,
    tasks: list[dict[str, Any]],
    db_path: str = _DEFAULT_DB,
) -> str:
    """Insert pending batch_tasks rows and return the new batch_id (uuid4 hex).

    Each item in *tasks* must contain at least ``task_id`` and ``brief``.
    Optional ``contention_class`` defaults to ``'FREE'``.

    Raises ValueError if *tasks* is empty or any item lacks required keys.
    Raises sqlite3.IntegrityError on duplicate (batch_id, task_id) -- not
    normally reachable with a fresh uuid4, but the UNIQUE constraint
    enforces it either way.
    """
    if not tasks:
        raise ValueError("tasks must be a non-empty list")
    batch_id = uuid.uuid4().hex
    now = time.time()
    rows: list[tuple[Any, ...]] = []
    for t in tasks:
        if "task_id" not in t or "brief" not in t:
            raise ValueError(f"each task must have 'task_id' and 'brief'; got {t!r}")
        rows.append((
            batch_id,
            lead_name,
            request_text,
            str(t["task_id"]),
            str(t.get("contention_class", "FREE")),
            str(t["brief"]),
            now,
        ))
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO batch_tasks
                (batch_id, lead_name, request_text, task_id,
                 contention_class, brief, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return batch_id


def running_count(lead_name: str, db_path: str = _DEFAULT_DB) -> int:
    """Return the number of tasks with status='running' for *lead_name*."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM batch_tasks WHERE lead_name=? AND status='running'",
            (lead_name,),
        ).fetchone()
    return int(row[0]) if row else 0


def claim_slot(
    batch_id: str,
    task_id: str,
    worker_agent_id: str,
    cap: int,
    db_path: str = _DEFAULT_DB,
) -> bool:
    """Atomically transition a pending task to running, respecting *cap*.

    The cap check and the row update are a single SQL statement executed
    under SQLite's write lock, so no explicit transaction or application-
    level lock is required.

    Returns True if the slot was claimed (status flipped to 'running').
    Returns False if:
      - the running count for this lead is already >= cap (backpressure), or
      - the task is no longer in 'pending' state (already claimed or terminal).
    """
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE batch_tasks
               SET status          = 'running',
                   worker_agent_id = ?,
                   started_at      = ?
             WHERE batch_id = ?
               AND task_id  = ?
               AND status   = 'pending'
               AND (
                     SELECT COUNT(*)
                       FROM batch_tasks AS b2
                      WHERE b2.lead_name = (
                              SELECT lead_name
                                FROM batch_tasks
                               WHERE batch_id = ?
                                 AND task_id  = ?
                            )
                        AND b2.status = 'running'
                   ) < ?
            """,
            (worker_agent_id, time.time(), batch_id, task_id, batch_id, task_id, cap),
        )
    return cur.rowcount == 1


def mark_done(
    batch_id: str,
    task_id: str,
    result_summary: str,
    db_path: str = _DEFAULT_DB,
) -> None:
    """Mark a task as done and record result_summary + completed_at."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE batch_tasks
               SET status         = 'done',
                   result_summary = ?,
                   completed_at   = ?
             WHERE batch_id = ?
               AND task_id  = ?
            """,
            (result_summary, time.time(), batch_id, task_id),
        )


def mark_failed(
    batch_id: str,
    task_id: str,
    error_msg: str,
    db_path: str = _DEFAULT_DB,
) -> None:
    """Mark a task as failed and record error_msg + completed_at."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE batch_tasks
               SET status       = 'failed',
                   error_msg    = ?,
                   completed_at = ?
             WHERE batch_id = ?
               AND task_id  = ?
            """,
            (error_msg, time.time(), batch_id, task_id),
        )


def get_batch(batch_id: str, db_path: str = _DEFAULT_DB) -> list[dict[str, Any]]:
    """Return all task rows for *batch_id*, ordered by id."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM batch_tasks WHERE batch_id=? ORDER BY id ASC",
            (batch_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_active(lead_name: str, db_path: str = _DEFAULT_DB) -> list[dict[str, Any]]:
    """Return non-terminal task rows for *lead_name* (pending or running)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM batch_tasks
             WHERE lead_name = ?
               AND status IN ('pending', 'running')
             ORDER BY id ASC
            """,
            (lead_name,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _ok(data: Any) -> None:
    print(json.dumps({"ok": True, "data": data}))


def _err(msg: str) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)


def _cmd_create_batch(args: argparse.Namespace) -> int:
    try:
        tasks: list[dict[str, Any]] = json.loads(args.tasks_json)
    except (json.JSONDecodeError, ValueError) as exc:
        _err(f"invalid --tasks-json: {exc}")
        return 1
    if not isinstance(tasks, list):
        _err("--tasks-json must be a JSON array")
        return 1
    db = args.db or _DEFAULT_DB
    try:
        init_db(db)
        batch_id = create_batch(args.lead, args.request, tasks, db)
        _ok({"batch_id": batch_id})
        return 0
    except (ValueError, sqlite3.Error) as exc:
        _err(str(exc))
        return 1


def _cmd_claim(args: argparse.Namespace) -> int:
    db = args.db or _DEFAULT_DB
    try:
        claimed = claim_slot(args.batch, args.task, args.worker, int(args.cap), db)
        _ok({"claimed": claimed})
        return 0
    except (ValueError, sqlite3.Error) as exc:
        _err(str(exc))
        return 1


def _cmd_done(args: argparse.Namespace) -> int:
    db = args.db or _DEFAULT_DB
    try:
        mark_done(args.batch, args.task, args.summary or "", db)
        _ok({"status": "done"})
        return 0
    except sqlite3.Error as exc:
        _err(str(exc))
        return 1


def _cmd_fail(args: argparse.Namespace) -> int:
    db = args.db or _DEFAULT_DB
    try:
        mark_failed(args.batch, args.task, args.error or "", db)
        _ok({"status": "failed"})
        return 0
    except sqlite3.Error as exc:
        _err(str(exc))
        return 1


def _cmd_state(args: argparse.Namespace) -> int:
    db = args.db or _DEFAULT_DB
    try:
        rows = get_batch(args.batch, db)
        _ok({"batch_id": args.batch, "tasks": rows})
        return 0
    except sqlite3.Error as exc:
        _err(str(exc))
        return 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m claude_soma.parallelism.ledger",
        description="Batch-task ledger CLI for Phase-1 lead parallelism.",
    )
    p.add_argument(
        "--db",
        default=None,
        help=(
            "Override DB path (default: HERMES_ORCH_DB env var or "
            "/opt/claude-soma/registry.sqlite)"
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    cb = sub.add_parser("create-batch", help="Create a new batch of pending tasks.")
    cb.add_argument("--lead", required=True, help="Lead name (e.g. 'social-manager')")
    cb.add_argument("--request", required=True, help="Raw operator request text")
    cb.add_argument(
        "--tasks-json",
        required=True,
        help=(
            'JSON array of task objects, each with "task_id", "brief", '
            'and optional "contention_class" (default "FREE")'
        ),
    )

    cl = sub.add_parser("claim", help="Claim a pending task slot (atomic cap-check).")
    cl.add_argument("--batch", required=True, help="batch_id")
    cl.add_argument("--task", required=True, help="task_id")
    cl.add_argument("--worker", required=True, help="worker_agent_id")
    cl.add_argument("--cap", required=True, type=int, help="Concurrency cap")

    dn = sub.add_parser("done", help="Mark a task as done.")
    dn.add_argument("--batch", required=True)
    dn.add_argument("--task", required=True)
    dn.add_argument("--summary", default="", help="Result summary text")

    fl = sub.add_parser("fail", help="Mark a task as failed.")
    fl.add_argument("--batch", required=True)
    fl.add_argument("--task", required=True)
    fl.add_argument("--error", default="", help="Error message")

    st = sub.add_parser("state", help="Print all task rows for a batch as JSON.")
    st.add_argument("--batch", required=True)

    return p


def main() -> None:
    """Entry point for ``python -m claude_soma.parallelism.ledger``."""
    parser = _build_parser()
    args = parser.parse_args()
    try:
        dispatch = {
            "create-batch": _cmd_create_batch,
            "claim": _cmd_claim,
            "done": _cmd_done,
            "fail": _cmd_fail,
            "state": _cmd_state,
        }
        code = dispatch[args.command](args)
        sys.exit(code)
    except Exception as exc:  # fail-soft: never crash the caller with a traceback
        _err(f"unexpected error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
