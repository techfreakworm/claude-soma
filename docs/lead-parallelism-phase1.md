# Lead Parallelism -- Phase 1 (FREE-only spine)

> **Status:** IMPLEMENTED (2026-06-13).
> This doc covers Phase 1 only.  See the full plan at
> `docs/superpowers/plans/2026-06-12-lead-parallelism-plan.md` for the
> phased roadmap and critic addendum.

---

## What Phase 1 delivers

A durable `batch_tasks` ledger in `registry.sqlite` + FREE-task parallel
dispatch + a per-lead concurrency cap with backpressure.

What is explicitly NOT in Phase 1:

- Resource leases or `resource_leases` table (Phase 2).
- Playwright parallelism -- EXCLUSIVE tasks run serially in Phase 1 (Phase 2).
- `--continue` ledger-bootstrap on lead resume (Phase 3).
- Retry/timeout/reconciliation (Phase 2/3).

The flag `HERMES_LEAD_PARALLELISM` defaults to OFF.  A flag-off lead is
byte-identical to the legacy serial baseline: nothing in `spawner.py` or
`server.py` references the new package, and importing the module has no
side effects.

---

## Architecture

### Ledger location

`HERMES_ORCH_DB` (default `/opt/claude-soma/registry.sqlite`) -- the same
file already shared by the orchestrator (`registry.py`) and the hermes-api
(`notify_store.py`).  The ledger is a third logical tenant of a file that
is designed for concurrent access.

### SQLite cross-process safety

The lead is a separate process; the orchestrator's `threading.Lock`
provides no protection for lead writes.  Two mechanisms cover this:

1. `PRAGMA journal_mode=WAL` -- allows concurrent readers while a single
   writer holds the WAL write lock.  Set on every connection open;
   persists on the file once set.

2. `PRAGMA busy_timeout=5000` -- on write contention, SQLite retries for
   up to 5 s before raising `OperationalError`.  Sufficient for all
   Phase-1 workloads.

Each `batch_tasks` row is written exclusively by the lead that created
the batch (identified by `lead_name`).  Last-writer-wins per row is
acceptable and safe: concurrent leads write different rows and cannot
corrupt each other's state.

No `flock` is required for Phase 1.  `flock` is reserved for Phase 2
lease mutations.

### Atomic cap-claim

`claim_slot()` uses a single `UPDATE` statement with a correlated
sub-select.  SQLite executes this atomically under its write lock:

```sql
UPDATE batch_tasks
   SET status='running', worker_agent_id=?, started_at=?
 WHERE batch_id=? AND task_id=? AND status='pending'
   AND (
     SELECT COUNT(*) FROM batch_tasks AS b2
      WHERE b2.lead_name = (
          SELECT lead_name FROM batch_tasks WHERE batch_id=? AND task_id=?
      )
        AND b2.status='running'
   ) < ?
```

`rowcount == 1` means claimed.  `rowcount == 0` means at cap (backpressure)
or already claimed (idempotent).

---

## Task classification

| Class | Definition |
|---|---|
| `FREE` | Stateless: research, drafting, file edits, web lookups, analysis, code. |
| `EXCLUSIVE` | Anything touching a shared stateful resource: Playwright, image-gen, voice, shared file/queue. |

Phase-1 rule: EXCLUSIVE tasks run serially (done by the lead inline).
Only FREE tasks are dispatched as background `Agent` subagents.
When in doubt, classify as `EXCLUSIVE`.

---

## Lead convention (flag-enabled path)

The full step-by-step protocol is in `skills/parallel-tasks/SKILL.md`.
Summary:

1. Check `HERMES_LEAD_PARALLELISM` -- if not `1`, operate serially.
2. Classify each task as FREE or EXCLUSIVE.
3. `create-batch` in the ledger (CLI).
4. For FREE tasks: `claim` (respecting cap) then `Agent(run_in_background=True)`.
5. EXCLUSIVE tasks: run yourself, one at a time.
6. As workers return: `done` or `fail` in the ledger; dispatch next pending FREE.
7. When all terminal: synthesise consolidated report to the operator.

---

## Module and CLI reference

### Python API

```python
from claude_soma.parallelism.ledger import (
    init_db,        # create table if not exists
    create_batch,   # insert pending rows, return batch_id
    running_count,  # count status='running' for a lead
    claim_slot,     # atomic cap-checked transition pending->running
    mark_done,      # set status='done' + result_summary
    mark_failed,    # set status='failed' + error_msg
    get_batch,      # all rows for a batch_id
    get_active,     # pending+running rows for a lead
)
```

### CLI (invoked by the lead via Bash)

```
python -m claude_soma.parallelism.ledger [--db PATH] <command> [args]

Commands:
  create-batch  --lead LEAD --request TEXT --tasks-json JSON_ARRAY
  claim         --batch BATCH_ID --task TASK_ID --worker WORKER_ID --cap N
  done          --batch BATCH_ID --task TASK_ID [--summary TEXT]
  fail          --batch BATCH_ID --task TASK_ID [--error TEXT]
  state         --batch BATCH_ID
```

All commands print JSON to stdout: `{"ok": true, "data": {...}}` on
success, `{"ok": false, "error": "..."}` on failure (to stderr, non-zero
exit).

---

## Enabling on a pilot lead

Phase 1 is gated by a per-lead environment variable.  The default is OFF:
all existing leads are unaffected.

### Step 1 -- create a systemd drop-in for the pilot lead

```bash
LEAD=social-manager   # adjust to your pilot lead name
DROPIN=/etc/systemd/system/claude-soma-lead-${LEAD}.service.d

sudo mkdir -p "${DROPIN}"
sudo tee "${DROPIN}/parallelism.conf" <<'EOF'
[Service]
Environment="HERMES_LEAD_PARALLELISM=1"
Environment="HERMES_LEAD_TEAM_CAP=3"
EOF
sudo systemctl daemon-reload
```

`HERMES_LEAD_TEAM_CAP` is optional; omit it to use the default of 3.

### Step 2 -- add the skill to the lead brief

In the lead's brief (the `brief` field passed to `spawn_project` or the
lead's template), append:

```
When HERMES_LEAD_PARALLELISM=1, follow skills/parallel-tasks/SKILL.md
for multi-task operator requests.
```

The skill file is at `skills/parallel-tasks/SKILL.md` in the repo root
(resolved via the lead's working directory, which is `/opt/claude-soma`).

### Step 3 -- restart the lead

Kill the existing lead instance and let the orchestrator respawn it:

```bash
# Via the orchestrator MCP tool:
mcp__project-orchestrator__kill_project social-manager
# The orchestrator will respawn it on the next operator message.
```

Or if the lead is managed by a persistent systemd unit:

```bash
sudo systemctl restart "claude-soma-lead-${LEAD}.service"
```

### Step 4 -- verify

Send the pilot lead a multi-task request with at least two FREE tasks:

```
do these three things:
1. Research the top 5 trending AI papers this week and write a summary
2. Summarise the last 50 engagement replies into a themes report
3. Archive last month's screenshots to /tmp/archive
```

You should see:
- Three rows in `batch_tasks` with `status='pending'` initially.
- Up to `HERMES_LEAD_TEAM_CAP` rows flip to `status='running'` immediately.
- Each completes independently and sets `status='done'`.
- A consolidated summary sent to you once all three are terminal.

### Reverting

Remove the drop-in and reload:

```bash
sudo rm -rf "${DROPIN}"
sudo systemctl daemon-reload
```

The lead reverts to the legacy serial baseline on next respawn.  No DB
migration is required: the `batch_tasks` table remains but is never
written by a flag-off lead.

---

## DB schema

```sql
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
```

`batch_id` is a uuid4 hex string (32 chars).  There is no separate
`task_batches` table in Phase 1: `batch_id` and `request_text` are carried
as plain columns on each row (denormalised for simplicity; Phase 2 may add
a `task_batches` parent table if dashboard needs warrant it).

---

## What is deferred to later phases

| Item | Phase |
|---|---|
| `resource_leases` table + lease acquire/release | Phase 2 |
| Playwright serialisation (EXCLUSIVE-class parallel dispatch) | Phase 2 |
| `pw-refresh` / engagement-drip flock integration | Phase 2 |
| `--continue` ledger-bootstrap on lead resume | Phase 3 |
| Retry policy + `NEEDS_INPUT` escalation | Phase 2/3 |
| Timeout / heartbeat staleness detection | Phase 2 |
| Orchestrator `_reconcile_leases()` | Phase 2 |
| Dashboard batch/task/lease surface | Phase 2 |

---

## Flag-off guarantee

- `spawner.py` and `server.py` contain no references to `parallelism` or
  `HERMES_LEAD_PARALLELISM`.
- Importing `claude_soma.parallelism.ledger` has no side effects (no DB
  access, no table creation).
- `init_db()` is the only function that creates the table, and it must be
  called explicitly.
- A lead with `HERMES_LEAD_PARALLELISM` unset or `0` never loads this
  module; its behaviour is byte-identical to the pre-Phase-1 baseline.
