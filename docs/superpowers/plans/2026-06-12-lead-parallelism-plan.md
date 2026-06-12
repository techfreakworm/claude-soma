# FI-LEAD-PARALLELISM — Design & Implementation Plan (2026-06-12)

> **Status: DESIGN ONLY — PROPOSED, approval required before ANY code.**
> Per-lead concurrent task execution: a project-lead fans out throwaway
> parallel teammates, tears them down on completion, and retains a durable
> task ledger — with MCP-clash avoidance (Playwright especially) as the
> load-bearing constraint. Operator-requested 2026-06-12. Produced via a
> subagent-driven planning pass (4 grounded research streams: spawn-mechanism,
> task-ledger, MCP-contention, division+teardown -> synthesized plan ->
> completeness/safety critic). PLANNING ONLY — no source file is to be
> modified until this plan is approved. Read the Critic Addendum at the
> bottom for corrections + the recommended first implementation steps.

---

# FI-LEAD-PARALLELISM — Design & Implementation Plan

> **Status: DESIGN ONLY — APPROVAL REQUIRED BEFORE ANY CODE.** No source file in `/home/ubuntu/projects/soma-improver/claude-soma` is to be modified until this plan is reviewed and signed off. Every claim below was verified against the live tree; file:line citations are inline.

---

## 1. Executive Summary + Core Problem

A claude-soma **project lead** is a single Claude session spawned by `spawner.py` as a transient, cgroup-isolated, tmux-wrapped systemd unit (`spawn_background_lead`, `spawner.py:260`+), launched with `--setting-sources user,project,local` (`spawner.py:318`) and `--setenv CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (`spawner.py:169`). It drains its inbox **serially**: one operator task at a time, blocking until that task finishes before starting the next.

Two facts are already true and are **not** the problem:
- **Different leads already run in parallel** — the orchestrator spawns many, capped at `MAX_CONCURRENT=6` (`server.py:32`), with liveness reconciliation that flips dead-but-active rows to `dead` so ghosts stop counting against the cap (`server.py:57–73`).
- **A lead can already call `Agent`/`Task` subagents** — and those subagents are explicitly exempted from the orchestrator gate (`orchestrator_gate.py:61`, `is_subagent_event`: any event carrying an `agent_id` is allowed through).

**What is missing:** a *managed, teardown-aware* way for **one** lead to run several operator tasks **concurrently**, with (a) a **durable ledger** that survives `--continue`/`--resume`, (b) **contention-class awareness** so that two workers never drive the same shared-stateful Playwright session at once, and (c) **bounded concurrency** with backpressure so a "do 20 things" request cannot fork-bomb the box.

This plan delivers that as a thin, flagged layer on top of primitives that already exist and are proven in production: the `Agent` subagent (used today by `social-x-writer` et al.), the SQLite-`registry.py` pattern (`isolation_level=None` + `threading.Lock`, `registry.py:77–83`), the FI-NOTIFY `lead_events` store (`notify_store.py`), and the engagement-queue `fcntl.flock(LOCK_EX)` sibling-lockfile primitive (`engagement-hourly-drip.py:117–137`, `queue_locked`).

---

## 2. Goals / Non-Goals

### Goals
- **G1.** One lead can accept N operator tasks and run the **independent** ones concurrently while **serializing** ones that share an exclusive resource (chiefly the per-platform Playwright session).
- **G2.** A **durable task ledger** in `registry.sqlite` records every task's lifecycle (`pending → assigned → running → done/failed/skipped`) and survives lead crash + `--continue`/`--resume`.
- **G3.** **Bounded concurrency**: a per-lead worker cap with backpressure (queue the overflow; never spawn-storm).
- **G4.** **Clean teardown**: workers are reaped, exclusive leases are released on completion *or* death/timeout, and the orchestrator's existing reconciliation absorbs orphans.
- **G5.** Ships **behind a per-lead flag**, enabled on exactly **one** lead first, with a done-when gate at each phase.

### Non-Goals (explicitly out of scope)
- **NG1. Not changing cross-lead parallelism.** The `MAX_CONCURRENT=6` orchestrator-level model (`server.py:32`) and the spawn/kill/reconcile lifecycle are untouched.
- **NG2. Not auto-posting.** FI-NO-POST-WITHOUT-APPROVAL stands. Parallelism speeds up *drafting/preparation*; the human-approval gate before any outbound post is unchanged. A worker may *draft* in parallel; it may **not** publish without the existing approval step.
- **NG3. Not building now.** This is a plan. No code until approved.
- **NG4. Not nested leads.** We are not spawning child orchestrator-managed leads (the "Option 3" spawner path). Parallelism stays *inside* one lead's session via `Agent` subagents.
- **NG5. Not per-worker browser-profile isolation in v1.** Deferred to a later, evidence-gated phase (see §6, §7).

---

## 3. Architecture

### 3.1 Chosen spawn mechanism — backgrounded `Agent` subagents (inside the lead's own session)

**Decision: the lead dispatches each parallel task as an `Agent`/`Task` subagent within its own session.** Rejected alternatives: native agent-teams split panes (kept as a *future* UX upgrade, not v1) and orchestrator-spawned nested leads (rejected as over-engineered — full systemd units per task, no structured result API, raw tmux send-keys dispatch).

Why `Agent` subagents win for v1:
- **Proven in this repo.** The social writers (`social-x-writer`, `social-linkedin-writer`, etc.) already run as dispatched subagents.
- **Gate inheritance is already solved.** `orchestrator_gate.py:61` exempts any event with an `agent_id`; dispatched subagents carry one, so a worker can call Playwright / write files / fire notify events without the gate denying it. **No gate change required.** (Verified: `is_subagent_event`, `orchestrator_gate.py:51–66`.)
- **cgroup inheritance is automatic.** Subagents are threads/children of the lead's `claude` process, which lives inside the lead's transient systemd scope — they run inside the same cgroup with no extra wiring.
- **Structured result handoff + automatic reaping.** The subagent returns a result string to the lead and is cleaned up by Claude Code's agent supervisor; the lead does not manage OS processes.

The cost — the lead must hold results in its context until synthesis — is mitigated by writing each result to the durable ledger *as it completes* (event-driven), so context pressure never loses state.

### 3.2 The task ledger (schema + durability)

The ledger lives in **`registry.sqlite`** (`HERMES_ORCH_DB`, default `/opt/claude-soma/registry.sqlite`). This is a deliberate, verified choice: that exact file is *already* a shared multi-writer DB — `registry.py` opens it from the orchestrator process (`registry.py:78`) and `notify_store.py` opens the **same path** from the hermes-api process (`notify_store.py:11,79`), both with `isolation_level=None`. The ledger is a third logical tenant of a file that is already designed for concurrent access. Co-locating it means the dashboard and reconciler reach task state with no new datastore.

Three new tables (mirroring the existing `CREATE TABLE IF NOT EXISTS` style at `registry.py:17,35,51`):

```sql
CREATE TABLE IF NOT EXISTS task_batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_name     TEXT NOT NULL,
    operator_id   TEXT NOT NULL,        -- Telegram user_id / channel-session agent_id
    request_text  TEXT NOT NULL,        -- raw multi-task operator request
    created_at    REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','partial_complete','complete','failed','aborted'))
);

CREATE TABLE IF NOT EXISTS batch_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id         INTEGER NOT NULL REFERENCES task_batches(id),
    lead_name        TEXT NOT NULL,
    task_id          TEXT NOT NULL,            -- human-facing, unique within batch
    contention_class TEXT NOT NULL,            -- 'FREE' | 'PLAYWRIGHT-X' | ...
    brief            TEXT NOT NULL,            -- the worker prompt
    worker_agent_id  TEXT,                     -- Agent handle once dispatched
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','assigned','running','done','failed','skipped')),
    result_summary   TEXT,
    result_ref       TEXT,                     -- path/URL/JSON pointer (<=500 chars)
    error_msg        TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    max_retries      INTEGER NOT NULL DEFAULT 1,
    created_at       REAL NOT NULL,
    assigned_at      REAL,
    started_at       REAL,
    last_heartbeat   REAL,
    completed_at     REAL,
    UNIQUE(batch_id, task_id)
);

CREATE TABLE IF NOT EXISTS resource_leases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name        TEXT NOT NULL UNIQUE,    -- 'PLAYWRIGHT-X', 'PLAYWRIGHT-LINKEDIN', ...
    lead_name         TEXT,                    -- holder's lead
    holder_agent_id   TEXT,                    -- worker holding it (NULL = free)
    acquired_at       REAL,
    expires_at        REAL,                    -- acquired_at + ttl
    queue_depth       INTEGER NOT NULL DEFAULT 0
);
```

**Concurrency / write-serialization.** Two layers, both already proven here:
1. **Intra-process:** every write goes through the registry's `threading.Lock` (`self._lock`, `registry.py:77`) on `isolation_level=None` autocommit — identical to every existing method (`registry.py:114,130,154,…`). This serializes the orchestrator process's own threads.
2. **Cross-process (lease mutation only):** because three *processes* touch this file, lease acquire/release — the one operation where a lost update is catastrophic — is additionally guarded by `fcntl.flock(LOCK_EX)` on a **sibling lockfile**, reusing `queue_locked` verbatim (`engagement-hourly-drip.py:117`). The lockfile inode is stable (never replaced), which is the exact property the existing comment relies on (`engagement-hourly-drip.py:124–133`). Lock path: `<registry.sqlite>.lease.lock` (or per-class `…/lease-<class>.lock` if contention measurement later warrants finer granularity).

**Durability across `--continue`/`--resume`.** The ledger is *authoritative*; the lead's transcript is not. On (re)spawn the lead runs a **ledger-reconciliation bootstrap** (injected via the lead brief / a `task-ledger` skill): query `get_active_batch(lead_name)` and for each non-terminal task —
- `running` with stale `last_heartbeat` (older than the heartbeat timeout) and a dead `worker_agent_id` → mark `failed` ("no heartbeat on resume; assumed crashed"), release any lease it held, then apply retry policy;
- `running` + live worker → keep supervising;
- `pending`/`assigned`-but-unstarted → re-dispatch.

This is the same survives-crash pattern already used for team context at `server.py:227–305` (`resume_project_impl` reads registry state before re-spawn) and is why the ledger must be on disk, not in transcript.

### 3.3 Contention-class model + lease/scheduler

Every task is tagged with a **contention class** — the exclusive resource it needs. The load-bearing fact (verified via commits 45b600a, 920f506 referenced in ground truth + the live engagement layer): **each authenticated Playwright MCP drives ONE shared, stateful browser session with a per-platform `storageState` profile**, and weekly `pw-refresh` keeps it warm. Two workers on the same Playwright MCP at once = interleaved navigations + `storageState` write races + bot-detection trips. Therefore Playwright sessions are **EXCLUSIVE**; pure compute/IO is **FREE**.

| Class | Exclusive resource | Concurrent holders |
|---|---|---|
| `FREE` | none | unbounded (up to the worker cap) |
| `PLAYWRIGHT-X` | X browser session / `state-x.json` | 1 |
| `PLAYWRIGHT-X-ARTICLE` | **same** `state-x.json` as `PLAYWRIGHT-X` → aliased to one lease | 1 (shared with X) |
| `PLAYWRIGHT-LINKEDIN` | LinkedIn session / `state-linkedin.json` | 1 |
| `PLAYWRIGHT-MEDIUM` | Medium session / `state-medium.json` | 1 |
| `PLAYWRIGHT-BASE` | base/unauthenticated browser | 1 |
| `TELEGRAM-SEND` | bot send quota | 1 |

> **Critical aliasing rule:** `playwright-x` and `playwright-x-article` write the *same* `state-x.json`. They MUST map to a **single** lease (`PLAYWRIGHT-X`), or two "different" workers will still collide on one file. This is encoded in the class→lease map, not left to the scheduler.

**Lease protocol** (managed by the lead, not the worker — workers just do the work):
- **acquire(class, agent_id, ttl)** — under `flock` + `_lock`: if the class row is free or expired, set holder + `expires_at = now + ttl`; else enqueue (`queue_depth += 1`) and the task stays `pending`.
- **release(class, agent_id)** — idempotent; clears holder, then dequeues the next waiter for that class and dispatches it.
- **default lease TTL: 3600 s (1 h)**, operator-tunable. A held-but-expired lease whose holder is dead is force-released by reconciliation.

### 3.4 How they compose — component diagram (text)

```
                         OPERATOR (Telegram / channel session)
                                   │  "do these 5 things"
                                   ▼
        ┌───────────────────────────────────────────────────────────────┐
        │  PROJECT LEAD  (single claude session; transient systemd scope; │
        │  cgroup-isolated; AGENT_TEAMS=1; lead-mcp.json: NO telegram /    │
        │  hermes-api / orchestrator MCP)                                  │
        │                                                                  │
        │   ┌──────────────┐   classify    ┌───────────────────────────┐  │
        │   │ TASK-DIVISION │──────────────▶│   SCHEDULER + CAP GATE     │  │
        │   │  (parse →     │               │  cap=HERMES_LEAD_TEAM_CAP  │  │
        │   │   class tag)  │               └───────────┬───────────────┘  │
        │   └──────────────┘                            │                  │
        │            FREE tasks ──────────────┐         │ EXCLUSIVE tasks  │
        │                                      ▼         ▼                  │
        │                          ┌────────────┐  ┌─────────────────┐     │
        │   Agent(run_in_bg) ×k →  │  WORKER A  │  │ lease_acquire(X) │     │
        │                          │  (FREE)    │  │  → WORKER C (X)   │     │
        │                          │  WORKER B  │  │  queued: D (X)…   │     │
        │                          └─────┬──────┘  └────────┬─────────┘     │
        │                                │ result            │ result        │
        └────────────────┬───────────────┴───────────────────┴─────────────┘
                         │ ledger writes (event-driven)   │ FI-NOTIFY events
                         ▼                                 ▼ (notify_orchestrator → :9100)
        ┌────────────────────────────────────┐   ┌──────────────────────────────┐
        │  registry.sqlite  (HERMES_ORCH_DB)  │   │  lead_events  (SAME FILE,     │
        │  ───────────────────────────────    │   │  hermes-api process,          │
        │  projects / team_members / routines │   │  notify_store.py)             │
        │  + task_batches                     │   │  STARTED/MILESTONE/COMPLETED/ │
        │  + batch_tasks   (NEW)              │   │  NEEDS_INPUT/ERROR            │
        │  + resource_leases                  │   └───────────────┬──────────────┘
        │  guard: _lock (intra) + flock(lease)│                   │ drained to operator DM
        └──────────────────┬──────────────────┘                  ▼
                           │ read                          OPERATOR sees progress
                           ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │ ORCHESTRATOR  (server.py)  — reconcile loop (server.py:57)         │
        │  • dead-vs-killed lead reconciliation (existing)                   │
        │  • NEW: _reconcile_leases() — force-release expired leases whose   │
        │    holder is a dead lead; absorb orphaned batches                  │
        │  • NEW: on resume, lead bootstrap re-reads ledger (§3.2)           │
        └──────────────────────────────────────────────────────────────────┘
```

Two write paths, two owning processes, one file. The ledger (orchestrator-process writes via `registry.py`) and FI-NOTIFY events (hermes-api-process writes via `notify_store.py`) stay in their existing lanes; the **only** new cross-process mutation is the lease, which is why the lease is the one thing wrapped in `flock`.

---

## 4. The lead's task-division algorithm

**Input:** a multi-task operator request. **Output:** a batch ledger row + immediate dispatch of FREE tasks and the head of each EXCLUSIVE class; the rest queued.

```
schedule(request):
  tasks  = parse(request)               # split into discrete units
  for t in tasks: t.class = classify(t) # heuristic + brief hints (see below)
  batch  = ledger.create_batch(lead_name, operator_id, request)
  for t in tasks: ledger.insert_task(batch, t)         # status=pending

  # 1) FREE — dispatch immediately, respecting the cap
  for t in tasks where t.class == FREE:
      if running_workers() >= LEAD_TEAM_CAP:
          leave t pending (backpressure); continue
      a = Agent(t.brief, model=opus, run_in_background=True)
      ledger.assign(t, a)                               # pending→assigned

  # 2) EXCLUSIVE — one head per *lease* (after class→lease aliasing), rest queue
  for lease_key, group in group_by_lease(EXCLUSIVE tasks):
      if running_workers() < LEAD_TEAM_CAP and lease_acquire(lease_key, head):
          a = Agent(head.brief, run_in_background=True)
          ledger.assign(head, a)
      enqueue the remainder on lease_key                # stay pending

  notify_orchestrator(STARTED, {batch summary})
  end turn (non-blocking)                               # workers run; lead supervises via events
```

**`classify(t)`** — heuristic first, brief-hint override second:
- explicit tag in the operator request (`[PLAYWRIGHT-X]`, `[FREE]`) wins;
- else keyword heuristic: "post/draft/scroll/engage on X" → `PLAYWRIGHT-X`; "X **article**/long-form" → `PLAYWRIGHT-X-ARTICLE` → **aliased to `PLAYWRIGHT-X` lease**; "LinkedIn" → `PLAYWRIGHT-LINKEDIN`; "Medium" → `PLAYWRIGHT-MEDIUM`; "train/compute/archive/summarize/research" → `FREE`;
- **default-safe fallback:** if a task *might* touch a browser but the platform is ambiguous, classify as the **base** Playwright lease (EXCLUSIVE) rather than FREE — never risk an unguarded shared-session collision.

### Worked example — "social-manager, do these 5 things"

> 1. Draft a 3-post **X thread** on this week's build log, hold for approval. `[PLAYWRIGHT-X]`
> 2. Draft an **X long-form Article** from the same notes. `[PLAYWRIGHT-X-ARTICLE]`
> 3. Draft a **LinkedIn** newsletter post. `[PLAYWRIGHT-LINKEDIN]`
> 4. **Summarize** the last 200 engagement replies into a themes report (no browser). `[FREE]`
> 5. **Archive** last month's screenshots to S3 + wipe local cache. `[FREE]`

Classification + lease mapping (cap = 3):

| # | Task | Class | Lease | t=0 action |
|---|---|---|---|---|
| 4 | Summarize replies | FREE | — | **dispatched** (worker α) |
| 5 | Archive screenshots | FREE | — | **dispatched** (worker β) |
| 1 | X thread | PLAYWRIGHT-X | `PLAYWRIGHT-X` | **acquires lease, dispatched** (worker γ) — *cap now 3/3* |
| 3 | LinkedIn newsletter | PLAYWRIGHT-LINKEDIN | `PLAYWRIGHT-LINKEDIN` | lease free, **but cap full → queued** |
| 2 | X Article | PLAYWRIGHT-X-ARTICLE | `PLAYWRIGHT-X` (aliased) | **queued** behind #1 on the *same* lease |

Execution unfolds:
- **t=0:** workers α, β, γ run concurrently. #1 and #2 can *never* run together (same `state-x.json` lease) — the aliasing rule prevents the collision the naive class model would miss. #3 is queued only because the *cap* is full, not because its lease is taken.
- **β finishes (archive):** cap drops to 2/3 → scheduler dispatches the next queued task whose lease is free → **#3 LinkedIn** acquires its lease and starts.
- **γ finishes (X thread, drafts held for approval per NG2):** releases `PLAYWRIGHT-X` → dequeues **#2 X Article**, which acquires the freed lease and starts.
- All five complete (drafts staged, awaiting the human approval gate for the X/LinkedIn outbound steps). Lead synthesizes the ledger and fires `COMPLETED` with a summary: *"5/5 prepared: 1 themes report, 1 archive done, 3 drafts staged for your approval."*

Net: 3-way parallelism throughout, **zero** same-session Playwright collisions, overflow cleanly backpressured.

---

## 5. Teardown + failure semantics

### Per-task teardown (happy path)
On a worker's `COMPLETED` (FI-NOTIFY event and/or Agent return):
1. `ledger.update(task, status=done, result_summary, result_ref, completed_at=now)`.
2. If the task held a lease → **release it**, then **dequeue + dispatch** the next waiter for that lease (subject to the cap).
3. Worker process needs no explicit kill — Claude Code's agent supervisor reaps the returned subagent.
4. If `count(pending) == 0` for the batch → set batch `complete` (or `partial_complete` if any task `failed`/`skipped`) and fire the batch-summary `COMPLETED`.

### Failure modes → actions

| Mode | Detection | Ledger action | Operator surface |
|---|---|---|---|
| Worker error | `ERROR` event / Agent returns error | `failed` + `error_msg`; release lease | retry-or-ask (below) |
| Worker crash / OOM | Agent dead + no return | `failed`; release lease | retry-or-ask |
| Task timeout | no `MILESTONE`/heartbeat past timeout (default ~30 min stuck; 1 h hard) | `failed` ("timeout"); release lease | `NEEDS_INPUT`: "X stuck — retry/skip/abort?" |
| Lease orphan (holder dead) | reconciler: `expires_at < now` AND holder lead dead | force-release; requeue/fail head | logged; auto-recovers |
| Operator cancel | operator DM "stop batch N" | all non-terminal → `skipped`; batch `aborted` | confirm summary |

### Retry policy
Per-task `max_retries` (default 1). On failure with `retry_count < max_retries`: increment, reset to `pending`, re-dispatch (re-acquiring the lease if EXCLUSIVE), fire a `MILESTONE` ("retrying task X, attempt n"). On exhaustion: leave `failed` and fire **`NEEDS_INPUT`** ("Task X failed N times — retry once more / skip / abort batch?"). The operator's reply maps to: bump `max_retries` + retry · `skipped` + continue · `aborted`.

### Lease-on-death / timeout (the hard cases)
- **Worker dies holding a lease:** the lease's `expires_at` bounds the damage. `_reconcile_leases()` (new, hooked into the existing reconcile loop at `server.py:57`) finds expired leases, double-checks the holder lead is dead via the existing `is_lead_alive` (`server.py:70`, `spawner.py`), and only then force-releases. `flock` makes the release a safe read-modify-write even if the dead worker's lockfile lingers.
- **Lead dies mid-batch:** the orchestrator's existing reconciliation flips the lead row to `dead` (`server.py:57–73`). On `--continue`/`--resume` (`resume_project_impl`, `server.py:227`), the lead's **ledger-bootstrap** (§3.2) re-reads `batch_tasks`: stale-`running` → `failed`+retry, `pending` → re-dispatch, `done` → logged and skipped. The ledger is the source of truth; the lost transcript is irrelevant.

### Registry reconciliation (composition with what exists)
We **extend**, not replace, the reconcile loop:
- existing: dead-vs-killed lead status; ghosts stop counting against the cap (`server.py:57–73`).
- **new:** `_reconcile_leases()` — force-release expired/dead-held leases; mark batches whose owning lead is dead as `failed`/`partial_complete` so the dashboard never shows a forever-`active` batch behind a dead lead (the exact bug class the existing reconciler was written to kill).

---

## 6. Playwright decision (the load-bearing one)

**Recommended: Option 1 — serialize-all-Playwright via per-platform exclusive leases; parallelize everything FREE.** This is v1.

**Why this and not the others:**
- The shared, stateful, per-platform `storageState` session is the real constraint (45b600a, 920f506; weekly pw-refresh; the live X/LinkedIn bot-detection fragility). The safest correct behavior is: **at most one worker per platform session at any instant.** Leases give exactly that, reusing the `flock` primitive already trusted for the engagement queue.
- It is the **simplest thing that is correct**, and it still delivers the common win: in a 5-task mixed batch, only the 1–2 Playwright tasks serialize; the FREE majority run in parallel (see §4).
- Per-platform (not one global "playwright") leases mean an X task and a LinkedIn task **can** run in parallel — different sessions, different `storageState` files, different leases. We get cross-platform parallelism for free without touching auth.
- It does **not** pretend to fix bot-detection — that fragility exists in serial mode too and is out of scope here.

**Rejected / deferred:**
- **Option 2 — per-worker browser contexts (`launchPersistentContext`, copied `storageState`).** Rejected for v1. It multiplies the auth surface: each context is a cold headless browser to the platform (exactly what LinkedIn punishes), each re-runs `pw-refresh` independently, and copies risk auth-skew/divergence. High complexity, medium-term fragility, balloons disk. Only revisit if metrics prove sustained demand for *same-platform* parallelism — and even then behind heavy guardrails.
- **Option 3 — orchestrator-spawned nested leads per task.** Rejected. Full transient systemd unit per task, its own tmux socket, no structured result API, dispatch via raw `tmux send-keys` (`server.py:160–176`). It *would* give true browser isolation, but it is the wrong tool for in-lead task parallelism; reserve the spawner for multi-hour orchestration workloads.

**The one rule that makes Option 1 safe:** the class→lease map collapses `PLAYWRIGHT-X-ARTICLE` into the `PLAYWRIGHT-X` lease because they share `state-x.json`. Without it, Option 1 silently reintroduces the very collision it exists to prevent.

---

## 7. Phased rollout

Everything is gated by a **per-lead flag** — proposed `HERMES_LEAD_PARALLELISM=1`, set per-lead via the spawn env (`--setenv`, alongside `spawner.py:169`). Default **off**: with the flag unset, a lead behaves exactly as today (serial drain). Enable on **one** lead first (the `social-manager`-style lead is the natural pilot — it has the richest mix of FREE + Playwright work).

### Phase 0 — Spec freeze & test scaffolding *(no behavior change)*
- Lock schema + the `class → lease` map (incl. the X/X-Article aliasing) + cap defaults in a reviewed spec doc.
- Stand up the test harness: a fake/echo `Agent` and a temp `registry.sqlite`.
- **Done-when:** spec approved; harness can create a temp DB and stub subagents. **Test:** none (doc/scaffold only).

### Phase 1 — Minimal viable (the core of this plan)
Scope: ledger tables + Registry methods; the division algorithm (classify → FREE-parallel / EXCLUSIVE-serialize); per-lead worker cap + backpressure; lease acquire/release with `flock`; teardown on completion; `--continue` ledger-bootstrap. Behind the flag, on one lead.
- **Done-when:** on the pilot lead, a 5-task batch (≥2 FREE, ≥2 same-platform Playwright) runs FREE tasks concurrently, serializes the same-lease Playwright tasks with **zero** overlapping same-session navigations (asserted via lease holder log), respects the cap (overflow stays `pending`), and after a forced `kill_project`+`resume` the ledger-bootstrap correctly fails stale-running tasks and re-dispatches pending ones. Flag-off leads are byte-for-byte unchanged.
- **Test strategy:**
  - *Unit* — `insert_task`/`assign`/`update`/`lease_acquire`/`lease_release` against a temp DB; assert a second `lease_acquire` on a held class queues; assert `flock` serializes two threads racing the same class.
  - *Integration* — drive the scheduler with the §4 worked example using stub `Agent`s; assert dispatch order, that #1 and #2 (aliased X lease) never overlap, and that #3 starts only when the cap frees.
  - *Resume* — write a batch with a `running`+stale-heartbeat task, simulate resume, assert it flips to `failed`→retry.
  - *Live smoke* — run the pilot lead on the real VPS with one genuine Playwright draft task + 2 FREE tasks; confirm no `storageState` write race in the pane log (`/var/log/claude-soma/<name>.log`).

### Phase 2 — Robustness: timeouts, retries, lease reconciliation
Scope: heartbeat/`MILESTONE` timeout detection; retry policy + `NEEDS_INPUT` escalation; `_reconcile_leases()` in the orchestrator loop (force-release expired/dead-held leases; mark dead-lead batches terminal); dashboard read-only surface for batches/tasks/leases (extends the existing teammate rendering, commits ce29862/1920bde).
- **Done-when:** a deliberately-hung worker is detected by timeout and surfaces `NEEDS_INPUT`; a `kill -9` of a worker holding the X lease is auto-recovered by reconciliation within one reconcile interval; the dashboard shows live batch/task/lease state.
- **Test:** unit (timeout math, retry transitions); fault-injection (kill a lease-holder; assert force-release + requeue); a reconcile-loop test asserting no batch stays `active` behind a dead lead.

### Phase 3 *(evidence-gated, only if metrics demand)* — lease generality + isolation
Trigger: telemetry from Phases 1–2 shows real, repeated demand for **same-platform** parallelism that serialization bottlenecks.
- Options, in order of preference: (a) finer-grained leases / a small pool where a platform genuinely supports >1 safe concurrent context; (b) only as a last resort, **Option 2** per-worker browser profiles for the specific platform that proves it — behind its own sub-flag, with auth-skew guards and cold-login monitoring.
- **Done-when:** the measured bottleneck is relieved without an increase in bot-detection/login-wall events in the pane logs.
- **Test:** A/B the isolated path vs. the serialized path on the pilot lead; gate promotion on the auth-failure rate **not** rising.

> No phase widens the blast radius beyond one lead until its done-when gate passes. Promotion to more leads is a config change (set the flag), reversible by unsetting it.

---

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| **Same-session Playwright collision** (the headline risk) | Per-platform EXCLUSIVE lease, 1 holder; X/X-Article aliased to one lease; ambiguous browser tasks default to the base EXCLUSIVE lease, never FREE. |
| **Lost ledger update across 3-process shared SQLite** | Intra-process `threading.Lock` (as today, `registry.py:77`) + cross-process `fcntl.flock(LOCK_EX)` on a stable sibling lockfile for lease mutations (`queue_locked`, `engagement-hourly-drip.py:117`). |
| **`--continue` loses in-flight state** | Ledger is authoritative + on-disk; lead bootstrap re-reads `batch_tasks` on (re)spawn (mirrors `resume_project_impl`, `server.py:227`). |
| **Lease orphaned by dead holder** | TTL-bounded lease + `_reconcile_leases()` force-release after double-checking `is_lead_alive` (`server.py:70`). |
| **Fork-bomb from "do 20 things"** | Per-lead cap `HERMES_LEAD_TEAM_CAP` (default 3) + backpressure (overflow stays `pending`); optional global ceiling later. |
| **Context pressure** holding many results | Write each result to the ledger as it lands (event-driven), not held in transcript. |
| **Gate denies worker tool calls** | None expected — subagents carry `agent_id` and are already exempt (`orchestrator_gate.py:61`); no gate edit. **Verify** in Phase 1 with a worker `send_tg_reply`/Playwright smoke. |
| **Accidental autonomous posting** (NG2) | Workers draft only; the human-approval gate before outbound posting is unchanged and unbypassed. |
| **Naive multi-resource task → deadlock** | v1 forbids a single task holding two leases; multi-platform work is split into per-platform tasks (each on one lease). |
| **Ledger bloat** | `cleanup_old_tasks(days)` purges terminal tasks; run on lead startup. |
| **False-stale timeout failures** | Timeout is operator-tunable; tune against Phase-1/2 empirical data before widening. |
| **Flag-off regression** | Flag defaults off → byte-identical legacy serial path; Phase-1 done-when explicitly asserts unchanged behavior. |

---

## 9. Resolved answers to the logged open questions

1. **Ledger durability across `--continue`/`--resume`** — **Resolved: durable in `registry.sqlite`** (`HERMES_ORCH_DB`, `/opt/claude-soma/registry.sqlite`), the same file `registry.py` and `notify_store.py` already share. The ledger is authoritative; a lead bootstrap re-reads `batch_tasks` on every (re)spawn and reconciles stale `running` rows (fail+retry) / `pending` rows (re-dispatch). Transcript loss is a non-issue.

2. **Spawn mechanism choice** — **Resolved: backgrounded `Agent`/`Task` subagents inside the lead's own session.** Proven (social writers use it), gate-exempt automatically (`orchestrator_gate.py:61`), cgroup-inherited automatically, structured result + auto-reaping. Native agent-teams = future UX upgrade; orchestrator-nested leads = rejected (over-engineered for in-lead parallelism).

3. **Completion-confirm semantics** — **Resolved: dual-signal, ledger-authoritative.** Primary = FI-NOTIFY `COMPLETED`/`ERROR` event (`notify_orchestrator` → `lead_events`); secondary = the Agent's structured return. The lead writes the terminal status to the ledger on the first signal; a task is "done" iff its `batch_tasks` row is terminal. Liveness for long tasks = periodic `MILESTONE` heartbeats; missing heartbeats past timeout → `NEEDS_INPUT`.

4. **Resource-class registry location + how new MCPs declare class** — **Resolved.** The authoritative `class → lease` map is a small, reviewed table co-located with the orchestrator (a constant in `project_orchestrator/` consumed by the lead's scheduler; the live lease *state* is the `resource_leases` table). **Default contract:** an MCP is `FREE` unless it appears in the map. A new MCP that drives a shared, stateful session (browser, single CLI process, shared write handle) **must** add a one-line entry mapping its tool-prefix to a lease key; MCPs that alias an existing resource (the `playwright-x-article` → `state-x.json` case) map to the **existing** lease, never a new one. This is documented in a `task-ledger`/contention-class skill so the rule travels with the code.

5. **cgroup / gate inheritance for teammates** — **Resolved, no new work.** Workers are `Agent` subagents = children of the lead's `claude` process inside the lead's transient systemd scope, so they share the lead's **cgroup** automatically. They carry an `agent_id`, so the **orchestrator gate exempts them** automatically (`orchestrator_gate.py:51–66`). `SOMA_ORCHESTRATOR_GATE_SUBAGENT=1` exists as a belt-and-suspenders fallback if a future subagent kind ever fails to surface `agent_id`. Phase 1 includes a smoke test to confirm a worker's gated tool call passes.

6. **Failure semantics** — **Resolved.** Per-task `max_retries` (default 1) with auto-retry, then `NEEDS_INPUT` escalation (retry/skip/abort). Timeouts via heartbeat staleness. Lease-on-death bounded by TTL + `_reconcile_leases()` force-release after an `is_lead_alive` double-check. Lead-death mid-batch absorbed by the existing dead-vs-killed reconciler (`server.py:57–73`) plus the resume bootstrap. Batches resolve to `complete` / `partial_complete` / `aborted`; the dashboard never shows an `active` batch behind a dead lead.

---

**Reaffirmed: this is design only. No code is to be written until this plan is approved.** Once approved, implementation proceeds in the phase order above, behind `HERMES_LEAD_PARALLELISM` on a single pilot lead, with each phase's done-when gate as the promotion criterion.

---

## Critic Addendum

Verified against the live tree (file:line cited). The plan is unusually well-grounded — the gate-exemption, the `flock` primitive, the dual-tenant DB, and the cgroup-inheritance claims all check out. But it has **four load-bearing errors**, one of which (no reconcile loop exists) silently voids most of the §5/§8 recovery story, and it front-loads more than it admits.

### A. CRITICAL — the "existing reconcile loop" the plan hangs recovery on DOES NOT EXIST as a loop

`_reconcile_active()` (server.py:57) is **not** a background timer. It runs **only on-demand**, inline inside `spawn_project_impl` (server.py:107), `list_projects_impl` (server.py:142), and `resume_project_impl` (server.py:233). There is no `threading.Thread`, no `while True`, no asyncio task (grep confirms: the only four references are the def + those three call sites). 

Consequences that break the draft:
- §5/§8 promise `_reconcile_leases()` force-releases a dead-held lease "**within one reconcile interval**." There is no interval. A worker that `kill -9`s while holding `PLAYWRIGHT-X` leaves that lease held until the *next time some operator happens to spawn/list/resume a project* — which on a quiet box can be hours or never. The TTL (`expires_at`) is then the *only* real recovery path, making the TTL load-bearing, not a backstop.
- **Correction:** either (a) make TTL-expiry the primary release mechanism and have the *acquiring* code lazily steal any lease whose `expires_at < now` (no reconciler needed — check-and-steal under `flock` on every acquire), or (b) add a genuine periodic sweeper. Option (a) is simpler and is what Phase 1 should do; drop all "reconcile loop" language until/unless a real loop is built. This also means a held-but-expired lease must be **stealable by the next acquirer**, not just "force-released by a reconciler."

### B. CRITICAL — the lead process cannot call a lease method that lives in the orchestrator/hermes-api

The plan puts the ledger + lease logic "in `registry.py`" (orchestrator process) and reuses `queue_locked` from `notify_store.py` (hermes-api process). But **leads do not load the orchestrator or hermes-api MCPs** — `LEAD_MCP_CONFIG_DEFAULT` is explicitly "the bot's `.mcp.json` MINUS hermes-api and project-orchestrator" (spawner.py:62-70). The lead (and its `Agent` workers) therefore have **no MCP tool** to call `lease_acquire`. They can only touch the DB the way the lead already touches it: via `HERMES_ORCH_DB` path + a **direct `sqlite3` connection opened inside the lead's own process** (exactly what `_estimate_context_tokens` does, spawner.py:393-406).

This is fine — but it means:
- The lease/ledger code must ship as a **standalone module importable by the lead** (a script under `scripts/` or a tiny lib the lead invokes via `Bash(python ...)` or a new lead-side MCP added to `lead-mcp.json`), **not** as `Registry` methods on the orchestrator's singleton. The orchestrator's `threading.Lock` (registry.py:77) gives **zero** protection here because the lead is a *different process* with its own connection.
- Therefore **every** ledger write from a lead — not just lease mutation — crosses the 3+ writer boundary on autocommit (`isolation_level=None`). The plan's "intra-process `threading.Lock` serializes writes" claim (§3.2 layer 1, §8) is **false for the lead's writes**. SQLite's own file lock is the only thing serializing them, and on the default rollback journal a write contends with readers and can throw `SQLITE_BUSY`. **Add `busy_timeout` (PRAGMA) and ideally WAL mode**, and wrap *all* batch_tasks writes (not just leases) in `flock` if you want them race-free, OR accept that batch_tasks writes are last-writer-wins per row (acceptable since each task row has a single writer — the lead — but state this explicitly).

### C. CRITICAL — `pw-refresh` is a 4th, unsynchronized writer to `state-<name>.json`

The plan's threat model is "two workers / two teammates on the same session." It misses that **`claude-soma-pw-refresh.timer` → `pw-refresh.js` runs on a schedule as a separate process** and calls `chromium.launchPersistentContext(PROFILE)` then writes `state-${plat.name}.json` (pw-refresh.js:60,82-88). It takes **no flock** on anything the lease protects. So even with perfect lease discipline among leads/workers, the refresh timer can fire **mid-draft** and:
- open a *second* browser context against the same profile dir (the exact `launchPersistentContext` contention the plan rejects Option 2 for), and
- overwrite `state-x.json` underneath an in-flight worker.

Worse: this is **cross-lead and cross-process** — the lease table only governs entities that *opt into* taking the lease. A timer that knows nothing about leases is unaffected. **Two different leads** hitting the same browser (the prompt's explicit worry) is the same class of bug: lead-B's workers take leases in the *same* `resource_leases` table, so two leads *are* mutually excluded **iff** they share one DB and one lease module — but only if both are flagged on and both route through the identical lease code. The plan enables the flag on one lead, which accidentally makes it safe for now, but §7's "promotion to more leads is just a config change" is the moment cross-lead collision becomes live, and **nothing makes pw-refresh participate**.
- **Required additions:** (1) `pw-refresh.js` (and `engagement-hourly-drip`, which drives the same sessions, drip:596,664) must acquire the platform lease before touching `state-<name>.json`, or the lease must live at a layer all four writers already pass through (a sibling `state-<name>.json.lock` via `flock`, which pw-refresh and the engagement node scripts can take with one line). (2) Make the lock key the **state file path**, not an abstract class name — that's the actual shared resource, and it's the thing pw-refresh and the engagement scripts already know about. This also fixes the X/X-Article aliasing for free (same file ⇒ same lock).

### D. HIGH — `is_lead_alive` fails *toward* "alive," which inverts the lease-reclaim safety the plan assumes

The plan's force-release "double-checks the holder lead is dead via `is_lead_alive`." But `is_lead_alive` returns **`True` on any tmux/subprocess error or timeout** (spawner.py:533-535, by deliberate design: "a false 'dead' … is a worse outcome"). So under load or a tmux glitch, a genuinely-dead lease-holder reads as *alive*, and reconciliation **refuses to reclaim** the lease — exactly when you most need it reclaimed. Combined with finding A (no loop), a lease can wedge a platform indefinitely. **Mitigation:** rely on TTL-expiry as the authority for reclaim (time-based, can't be fooled by a flaky liveness probe); use `is_lead_alive` only as an *optional early* release, never as a gate that can *block* a TTL-expired steal.

### E. MEDIUM — deadlock/starvation gaps in the lease/cap interaction

- **Cap-vs-lease priority inversion / starvation:** §4 dispatches FREE tasks until the cap fills, then EXCLUSIVE heads only "if `running_workers() < CAP`." A batch heavy on long FREE tasks can hold the cap full indefinitely while a 1-second X task starves behind it, lease sitting free. The worked example hides this (it happens to free up). **Add:** reserve ≥1 cap slot for lease-holding (EXCLUSIVE) tasks, or schedule EXCLUSIVE heads *before* draining FREE overflow.
- **Dispatch-then-record ordering (crash window):** §4 does `Agent(...)` **then** `ledger.assign`. If the lead dies between the two, the worker exists with no ledger row → an orphan invisible to resume-bootstrap. **Invert:** write `assigned` (and `lease acquired`) to the ledger *before* dispatching, so the durable record always precedes or equals reality. Same for lease acquire: persist the lease row, *then* dispatch the worker that uses it.
- **Lease acquired but worker never starts:** if acquire succeeds and dispatch then fails/throws, the lease is held by a worker that doesn't exist. Needs the same TTL-steal as D; note it explicitly.

### F. MEDIUM — ledger compaction / `--continue` race is under-specified

§8 lists "ledger bloat → `cleanup_old_tasks(days)` … run on lead startup." A lead resuming mid-batch runs its bootstrap reconciliation **and** a cleanup at startup against a DB that the orchestrator and hermes-api are concurrently writing. If cleanup uses `DELETE` + the batch-status rollup reads concurrently, you can compute a wrong batch state. **Constrain:** cleanup may only purge **terminal** rows of **complete/aborted** batches, never touch an `active` batch, and must run *after* bootstrap reconciliation, under the same lock/flock as other writes. Also: a brand-new lead and a resuming lead both run "create schema if not exists" against the shared DB — confirm `CREATE TABLE IF NOT EXISTS` + the `ALTER TABLE … except OperationalError` idempotency pattern (registry.py:88-100) is mirrored, or two processes racing `executescript` on first deploy can collide.

### G. LOW/correctness — FI-NO-POST-WITHOUT-APPROVAL and the gate are actually FINE, with one nuance

Confirmed: subagents carry `agent_id` and are exempted (orchestrator_gate.py:61,78). **But the lead itself runs with `--dangerously-skip-permissions`** (spawner.py:306) and the gate hook is the *only* thing restricting it. The orchestrator_gate is the **bot's** hook; whether a *lead* even loads this PreToolUse hook depends on its settings sources (`user,project,local`, spawner.py:318) — verify the lead inherits orchestrator_gate at all. If it does **not**, then workers were never gated and "they inherit the exemption" is moot; if it **does**, the exemption means **workers can call `mcp__playwright*` freely** — which is the whole point, but it also means **nothing at the gate layer stops a worker from posting.** FI-NO-POST-WITHOUT-APPROVAL is enforced only by *prompt convention* (NG2), not by any hook. The plan should state that parallelism **widens the surface** of that prompt-only guarantee (N workers, each capable of an outbound Playwright action) and add a concrete guard: keep the *poster* skills (`social-x-poster` etc.) out of worker briefs, or add a publish-action denylist to a lead-side hook. Do not claim the gate protects against autonomous posting — it doesn't.

### H. Phasing front-loads risk (contradicts "no phase widens blast radius")

Phase 1 bundles: new schema + cross-process DB access from a new process context (B) + the `flock` lease + classify heuristic + cap/backpressure + **`--continue` ledger-bootstrap**. The bootstrap is the single hardest, least-reversible piece (it mutates durable state on every respawn based on liveness heuristics that fail-open, D), yet it's in the MVP. Meanwhile timeouts/retries/reconciliation are deferred to Phase 2 — so Phase 1 ships a system that can *acquire and lose* leases but cannot *reclaim* them. That's a strictly worse intermediate state than serial. **Re-cut:** Phase 1 = ledger + FREE-only parallelism + cap/backpressure (no leases, no Playwright, no bootstrap) — purely additive, trivially reversible, proves the durable-ledger + Agent-dispatch spine. Phase 2 = leases (with TTL-steal as primary reclaim) + Playwright serialization + the pw-refresh/engagement lock integration (C). Phase 3 = `--continue` bootstrap + retries/timeouts. This makes each phase's "flag-off is byte-identical" claim actually hold at the point of first lease use.

### Over-complications to cut from Phase 1
- `task_batches` **and** `batch_tasks` **and** `resource_leases` as three tables on day one is more than the FREE-only MVP needs. Start with `batch_tasks` (carry `batch_id` as a plain column + a `request_text`); add `resource_leases` only in the lease phase.
- `queue_depth` on `resource_leases` is premature — derive waiter count by `COUNT(*) WHERE status='pending' AND contention_class=?`; don't maintain a denormalized counter that can desync under the very races you're guarding against.
- Per-class lockfiles (`lease-<class>.lock`) "if contention later warrants" — drop the option entirely; one lock keyed on the **state-file path** (finding C) is simpler and correct.
- The text component diagram and the worked-example table are great for review but should not imply the scheduler is more than: parse → tag → INSERT pending → dispatch under (cap ∧ lease-free). Keep `classify()` dumb (explicit tag wins; default-to-EXCLUSIVE-base on any browser ambiguity) and resist the keyword taxonomy growing.

### Three highest-confidence first implementation steps (when approved)

1. **Ship the lease/ledger as a standalone lead-importable module keyed on the storageState file path, not as orchestrator `Registry` methods.** Put it where the lead can reach it in-process (a `scripts/lead_ledger.py` the lead calls via Bash, or a new entry in `lead-mcp.json`); open the shared DB with `isolation_level=None`, `check_same_thread=False`, **`PRAGMA busy_timeout`**, and guard lease + state-file mutation with `flock` on `<state-x.json>.lock` (the literal file pw-refresh/engagement already write). This single decision resolves findings B and C and makes the X/X-Article aliasing automatic.

2. **Make lease reclaim time-based (TTL-steal under `flock`), with `is_lead_alive` as an optional accelerator only — and write the ledger row before dispatching the worker.** `acquire()` = under `flock`: if row free OR `expires_at < now`, take it; else queue. No dependency on any background reconcile loop (finding A) and immune to the fail-open liveness probe (finding D). Order every operation durable-record-first (finding E).

3. **Build Phase 1 as FREE-only parallelism with the durable ledger + cap/backpressure and NO leases, NO Playwright, NO `--continue` bootstrap.** Done-when: a mixed batch of N FREE tasks runs concurrently up to `HERMES_LEAD_TEAM_CAP`, overflow stays `pending`, results land in `batch_tasks` event-driven, and a flag-off lead is byte-identical to today. This is the reversible spine; defer every lease/Playwright/resume mechanism (the parts that can wedge a platform) to a gated Phase 2/3.

Files inspected: `scripts/orchestrator_gate.py`, `src/claude_soma/mcp_servers/project_orchestrator/{registry.py,server.py,spawner.py}`, `src/claude_soma/mcp_servers/hermes_api/notify_store.py`, `scripts/engagement-hourly-drip.py`, `scripts/pw-refresh.js`, `systemd/claude-soma-pw-refresh.{service,timer}`.
