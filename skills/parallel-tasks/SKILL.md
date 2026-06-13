---
name: parallel-tasks
description: |
  Phase-1 FREE-only parallel task dispatch for a pilot lead.
  Load this skill (via the lead brief) only when HERMES_LEAD_PARALLELISM=1
  is set in the lead's environment.  When the flag is absent, ignore this
  skill entirely -- the lead must behave byte-identically to the serial
  baseline.
---

# parallel-tasks

## When this skill applies

Only activate this workflow when ALL of the following are true:

1. The environment variable `HERMES_LEAD_PARALLELISM` is set to `1`.
2. The operator request contains two or more clearly separable tasks.

If the flag is absent or the request is a single task, operate exactly as
before (serial, no ledger).

## Step 0 -- check the flag

```bash
echo "${HERMES_LEAD_PARALLELISM:-0}"
```

If the output is not `1`, stop here and handle the request serially.

## Step 1 -- classify each task

For every discrete task in the operator request, assign one of two
contention classes:

- `FREE` -- stateless work: research, drafting, file edits, web/HF
  lookups, code analysis, data processing, summarisation.  No shared
  browser session, no shared write handle, no voice synthesis.

- `EXCLUSIVE` -- anything that touches a shared, stateful resource:
  Playwright (any platform), image generation, voice synthesis, or any
  shared file or queue.  When in doubt, default to `EXCLUSIVE`.

Phase-1 rule: EXCLUSIVE tasks are NOT parallelised.  Run them yourself
one at a time after all FREE tasks are dispatched.

## Step 2 -- create a batch in the ledger

```bash
BATCH=$(
  HERMES_ORCH_DB="${HERMES_ORCH_DB:-/opt/claude-soma/registry.sqlite}" \
  python -m claude_soma.parallelism.ledger create-batch \
    --lead  "${HERMES_LEAD_NAME}" \
    --request "$(echo '<operator request text here>')" \
    --tasks-json '[
      {"task_id": "t1", "brief": "<worker prompt>", "contention_class": "FREE"},
      {"task_id": "t2", "brief": "<worker prompt>", "contention_class": "FREE"}
    ]' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['batch_id'])"
)
```

Record `$BATCH` -- you need it for every subsequent ledger call.

## Step 3 -- dispatch FREE tasks (respecting the cap)

The cap is `HERMES_LEAD_TEAM_CAP` (default 3).

For each FREE task:

1. Attempt to claim a slot:

   ```bash
   CLAIMED=$(
     python -m claude_soma.parallelism.ledger claim \
       --batch "$BATCH" --task "t1" --worker "worker-t1" \
       --cap "${HERMES_LEAD_TEAM_CAP:-3}" \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['claimed'])"
   )
   ```

2. If `CLAIMED` is `True`: dispatch via `Agent(prompt=<brief>, model="opus", run_in_background=True)` and record the agent handle as the worker.

3. If `CLAIMED` is `False` (at cap): leave the task pending.  Dispatch it
   once a running worker returns and frees a slot (see Step 5).

Never dispatch more workers than the cap allows.

## Step 4 -- EXCLUSIVE tasks run serially

Do each EXCLUSIVE task yourself, inline, one at a time, in the order the
operator listed them.  Do NOT dispatch them as background agents in Phase 1.
There are no leases or Playwright-parallelism mechanisms in Phase 1.

## Step 5 -- handle worker returns

As each background agent returns:

- Mark the task done in the ledger:

  ```bash
  python -m claude_soma.parallelism.ledger done \
    --batch "$BATCH" --task "t1" \
    --summary "<one-sentence result>"
  ```

- If the agent returned an error, mark it failed:

  ```bash
  python -m claude_soma.parallelism.ledger fail \
    --batch "$BATCH" --task "t1" \
    --error "<error text>"
  ```

- After marking done/failed, check for pending FREE tasks and dispatch
  the next one (claim-then-dispatch, same as Step 3).

## Step 6 -- consolidated report

When all tasks are in a terminal state (done, failed, or skipped):

1. Query batch state:

   ```bash
   python -m claude_soma.parallelism.ledger state --batch "$BATCH"
   ```

2. Synthesise a single reply to the operator summarising all results.
   The ledger is the authoritative record; do not rely on transcript alone.

## Phase-1 explicit limits

The following are NOT available in Phase 1 and must not be attempted:

- Resource leases or Playwright-parallelism (Phase 2).
- `--continue` ledger-bootstrap on lead resume (Phase 3).
- Retry or timeout escalation (Phase 2/3).
- Dispatching EXCLUSIVE tasks as background agents.

## Concurrency cap defaults

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_LEAD_PARALLELISM` | `0` (off) | Master flag; must be `1` to activate |
| `HERMES_LEAD_TEAM_CAP` | `3` | Max concurrent FREE workers |
