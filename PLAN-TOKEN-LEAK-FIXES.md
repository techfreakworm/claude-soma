# PLAN-TOKEN-LEAK-FIXES.md

**Status:** APPROVED end-to-end by the user 2026-06-02 (sleep-mode authorization, auto-restart window armed until 02:30 UTC / 08:00 IST 2026-06-02). Lead is executing autonomously.

**Generated:** 2026-06-02 by soma-improver. Methodology: sequential-thinking pass over the 13 leak candidates from `TOKEN-LEAK-AUDIT.md`; cluster by file-touch + restart-impact + hard deps; produce paste-ready sonnet+max+seq-thinking subagent briefs per cluster. Output dual-published to repo root + `/var/lib/claude-soma/relay/` for `https://files.mayankgupta.in/PLAN-TOKEN-LEAK-FIXES.md`.

**Scope:** all 13 candidates from `TOKEN-LEAK-AUDIT.md` (3 CRITICAL + 4 HIGH + 4 MEDIUM + 2 LOW). User pre-approval covers candidates #1–13 and any new leak surfaced mid-execution becomes candidate #14+ shipped in the same push.

**Out of scope:** anything in `FAR-FETCHED.md`. No new platforms, no Bluesky scope, no FI-PW.

---

## Part 1 — Dependency DAG

```
                     ┌──────────────────────────────────────────────┐
                     │ WAVE 1 — independent leak surfaces (parallel)│
                     ├──────────────────────────────────────────────┤
                     │ W1A: H1   notify_inject mark_read race        │
                     │ W1B: H2+F3 auto-restart marker + Bash deny    │
                     │ W1C: T1   usage_snapshot HTTP-direct          │
                     │ W1D: T2+L5 daily_status budget + rc busy hyst │
                     │ W1E: L4   channel /clear weekly cron          │
                     │ W1F: F1   routines run_routine filelock       │
                     │ W1G: T3   hermes_api SO_REUSEADDR + debounce  │
                     │ W1H: L1+F2+L2 resume hardening (3 leaks 1 PR) │
                     └──────────────┬───────────────────────────────┘
                                    │
                          channel + api restart
                                    │
                                    ▼
                     ┌──────────────────────────────────────────────┐
                     │ WAVE 2 — schema-dependent fix                │
                     ├──────────────────────────────────────────────┤
                     │ W2: L3   per-lead turn counter + reaper kill │
                     │           (adds projects.turn_count column)   │
                     └──────────────┬───────────────────────────────┘
                                    │
                          channel + api restart
                                    │
                                    ▼
                     ┌──────────────────────────────────────────────┐
                     │ WAVE 3 — consumer of L3's signal             │
                     ├──────────────────────────────────────────────┤
                     │ W3: ME-1  Telegram DM alarm worker           │
                     │           polls projects for >150k context   │
                     └──────────────┬───────────────────────────────┘
                                    │
                          api restart + frontend rebuild (no banner this round)
                                    │
                                    ▼
                              COMPLETED report
```

**Why this shape:**
- Wave 1 candidates are file-disjoint; 8 sonnet subagents can ship in parallel with rebase-on-rejection as the only coordination protocol.
- L3 introduces a registry schema column (`turn_count` on `projects`). Any concurrent Wave 1 subagent that also touched `registry.py` would conflict — therefore W1H (resume hardening) is briefed to ONLY touch `server.py` resume helper + `spawner.py`; nothing in `registry.py`.
- ME-1 reads `turn_count` (or its estimated-tokens companion) directly from the registry — so it strictly post-dates L3.

---

## Part 2 — Restart matrix

| Wave | Channel restart? | API restart? | Frontend rebuild? | Other |
|---|---|---|---|---|
| Wave 1 end | YES (W1H project_orchestrator MCP) | YES (W1F + W1G) | NO | systemd `daemon-reload` for new W1E weekly cron + W1C timer change |
| Wave 2 end | YES (project_orchestrator registry import) | YES (admin routes read registry) | NO | sqlite ALTER TABLE projects ADD COLUMN turn_count INTEGER DEFAULT 0 |
| Wave 3 end | NO | YES (new alarm worker thread in hermes_api) | YES (dashboard component deferred → frontend may or may not rebuild; if no FE change, skip) | — |

**Auto-restart sequencing:** at each wave boundary the lead pushes /opt → fires `RESTART REQUIRED MILESTONE` with explicit services list. Auto-trigger (armed by operator) picks up the event via the new `notify_inject.sh` hook, validates the auto-window, fires `auto-restart-services.sh` with validated service-name regex. Lead polls `systemctl show <svc> -p MainPID,ActiveEnterTimestamp` to confirm restart actually fired. If no restart within 3 min, emits `NEEDS_INPUT` (last resort).

**Note:** Wave 1's W1A fix (H1) makes restart cycles SAFE for the first time — the prior race could itself amplify under any restart. Order matters: ship H1 FIRST inside Wave 1 if any subagent serialization is forced.

---

## Part 3 — Coordination protocol for shared-file touchers

| File | Touched by |
|---|---|
| `scripts/notify_inject.sh` | W1A only |
| `scripts/auto-restart-services.sh` | W1B only |
| `scripts/orchestrator_gate.sh` | W1B only |
| `scripts/usage_snapshot.py` + its timer | W1C only |
| `scripts/daily_status.sh` | W1D only |
| `scripts/rc_url_refresh.py` | W1D only |
| `scripts/healthcheck.sh` + new cron unit | W1E only |
| `scripts/reaper.py` | W2 only |
| `src/claude_soma/api/routes/routines.py` | W1F only |
| `src/claude_soma/mcp_servers/hermes_api/server.py` | W1G + W3 (sequential, two waves apart) |
| `src/claude_soma/mcp_servers/project_orchestrator/server.py` | W1H only — W2 stays out via registry indirection |
| `src/claude_soma/mcp_servers/project_orchestrator/spawner.py` | W1H only |
| `src/claude_soma/mcp_servers/project_orchestrator/registry.py` | W2 only |

**Git hygiene (binding):** every subagent stages SPECIFIC paths only — no `git add -A`, no `git add .`. (Burned lesson from earlier waves where `git add -A` swept up unrelated WIP.) Push to `origin/main` directly. On non-fast-forward rejection: rebase, no force-push. On rebase conflict: STOP-AND-SURFACE to `/tmp/<TASK-ID>-STOP.md` and exit non-zero — do NOT improvise resolution.

**Author convention:** `Mayank Gupta <techfreakworm@gmail.com>` sole author. NO `Co-Authored-By`, NO Claude footer, NO emoji in commit message, NO emoji in code.

---

## Part 4 — Pre-decided defaults for the 5 open Part-D questions

Sleep-mode authorization means the lead picks defaults and surfaces what was chosen in the morning COMPLETED. The user can revert any of these if undesired:

1. **H1 fix shape:** move the `mark_read` POST to BEFORE the `auto-restart-services.sh` spawn block. Single atomic re-order; minimal risk.
2. **T1 fix shape:** direct HTTPS to `https://api.anthropic.com/v1/usage` using `Authorization: Bearer ${CLAUDE_CODE_OAUTH_TOKEN}`. Fallback if the OAuth scope doesn't permit `/usage` directly: revert the timer to daily (`OnCalendar=*-*-* 23:55:00`) and emit a follow-up NEEDS_INPUT in the morning.
3. **L3 cap:** trigger reaper-kill if `turn_count >= 50` OR `estimated_context_tokens >= 200_000`. Estimated context = rolling sum of payload byte lengths of that lead's `lead_events` over the last 24h × 0.25 (chars→tokens approximation). Tunable via `HERMES_LEAD_TURN_CAP` + `HERMES_LEAD_CONTEXT_CAP_TOKENS` env vars.
4. **ME-1 alarm surface:** Telegram DM via the existing channel-side Bot API path (matches `healthcheck.sh` NEEDS_REAUTH pattern). Dashboard banner deferred — open follow-up.
5. **L2 resume guard:** REFUSE `--resume` when estimated context > 200k tokens. Error message instructs the operator to kill + re-spawn fresh, or pass an explicit `force=True` arg. No silent strip.

---

## Part 5 — Per-candidate subagent briefs (paste-ready)

All briefs share this preamble (binding constraints repeated in each — see CLAUDE.md if you need fuller context):

> You are a sonnet implementation subagent on the soma-improver lead. Use `mcp__sequential-thinking__sequentialthinking` with `--effort max` to reason through the fix BEFORE writing any code. Work in `/home/ubuntu/projects/soma-improver/claude-soma`. Pull `origin/main` fresh, branch implicitly (work on main locally; we push direct), modify ONLY the listed files, run the listed tests, commit with `Author: Mayank Gupta <techfreakworm@gmail.com>` (NO `Co-Authored-By`, NO Claude footer, NO emoji), push to `origin/main`. On non-fast-forward rejection: rebase, try push again, on conflict STOP-AND-SURFACE to `/tmp/<TASK-ID>-STOP.md`. Use `git add <specific-paths>` only — never `-A`. Report the commit SHA + diffstat + pytest result.

### W1A — H1 fix (CRITICAL): notify_inject.sh mark_read-before-spawn

**Leak hypothesis:** the auto-restart helper is spawned at `scripts/notify_inject.sh:130–142` BEFORE `mark_read` is POSTed at lines 143–152. If `RESTART_SERVICES` contains `claude-soma-channel.service`, systemd kills the cgroup (incl. the running `notify_inject.sh`) before `mark_read` runs. The MILESTONE row keeps `hook_injected_at IS NULL`. On the next user prompt, the same MILESTONE re-fires the auto-restart. Loop until window expiry.

**Files to modify:**
- `scripts/notify_inject.sh`
- `tests/scripts/test_notify_inject.py` (add regression)

**Fix:** move the entire `mark_read` POST block (currently lines ~143–152) to immediately BEFORE the auto-restart spawn block (currently ~line 130). After moving, the order is: `get_uninjected` → `mark_read` → fire `auto-restart-services.sh` in background.

**Acceptance test (new):** mock `subprocess.Popen` for the `auto-restart-services.sh` call and `urllib` for `mark_read`. Assert `mark_read` POST occurs BEFORE `Popen(auto-restart...)`. Use `unittest.mock.patch` + call_args_list order assertion.

**Live verification (lead-side after push + restart):** insert a fake MILESTONE with progress="RESTART REQUIRED test_h1_verify"; trigger UserPromptSubmit; assert sqlite row has `hook_injected_at NOT NULL` before the channel SIGTERM.

### W1B — H2 + F3 fix (LOW): auto-restart sticky marker + Bash gate `claude` deny

**Leak hypothesis (H2):** `flock -n` at `scripts/auto-restart-services.sh:39–43` prevents concurrent fires but allows SEQUENTIAL double-fire — once the first invocation releases fd 9 after `systemctl restart` returns (~2s), a second invocation grabs the lock and restarts again. Window-bounded but compounds with H1 race.

**Leak hypothesis (F3):** `scripts/orchestrator_gate.sh:92–136` Bash deny block lists apt/pip/npm/git/docker/make/pytest/codex/ffmpeg/whisper-cli/curl/wget but `claude` is absent. A bot Bash turn emitting `claude -p "..."` passes unchallenged.

**Files to modify:**
- `scripts/auto-restart-services.sh`
- `scripts/orchestrator_gate.sh`
- `tests/scripts/test_auto_restart_services.py` (extend)
- `tests/scripts/test_orchestrator_gate.py` (extend)

**Fix (H2):** after acquiring `flock`, write a marker file `<lockfile>.fired-<window_utc>` containing the current `HERMES_AUTO_RESTART_WINDOW_UTC` value. At top of script, check if marker for current window already exists → exit 0 with log "already fired this window".

**Fix (F3):** add to the Bash case block: `claude) deny "Direct claude subprocess in Bash${REASON_TAIL}" ;;`.

**Acceptance test (new):** for H2, mock `flock` + `systemctl`, fire script twice in same window → assert second invocation exits without calling `systemctl`. For F3, run `orchestrator_gate.sh` with `Bash` command `claude -p hi` → assert exit code routes to deny.

### W1C — T1 fix (CRITICAL): usage_snapshot HTTP-direct, no claude subprocess

**Leak hypothesis:** `scripts/usage_snapshot.py:37` runs `subprocess.run([CLAUDE, "-p", "/usage", "--output-format", "json"], ...)` every 15 minutes after commit `f2bf274` bumped the timer. 96 cold claude session startups per day.

**Files to modify:**
- `scripts/usage_snapshot.py`
- `tests/scripts/test_usage_snapshot.py` (extend; remove subprocess-mock if any, add HTTP-mock)

**Fix:** replace the `subprocess.run([CLAUDE, "-p", "/usage", ...])` call with `urllib.request` (or `requests` if available) to `https://api.anthropic.com/v1/usage` with header `Authorization: Bearer {os.environ['CLAUDE_CODE_OAUTH_TOKEN']}` and `anthropic-version: 2023-06-01`. Parse the same JSON keys (`interactive_credits_used`, `interactive_credits_ceiling`, `agent_sdk_credits_used`, `agent_sdk_credits_ceiling`). Keep the existing `_to_f` extractor unchanged. If the HTTP call returns non-200 or auth fails, log to stderr + exit 1 (the systemd timer will simply skip this snapshot — no fallback to claude subprocess).

**Acceptance test (new):** mock `urllib.request.urlopen` to return a fake JSON usage payload; assert subprocess.run is NOT called with `claude`; assert usage.sqlite row is written.

**Live verification:** after deploy, `systemctl start claude-soma-usage-snapshot.service`; tail `journalctl -u claude-soma-usage-snapshot`; check that NO `claude` process spawned during the run (compare `ps auxf | grep claude` before/after); confirm `sqlite3 /opt/claude-soma/usage.sqlite "select * from daily_snapshots order by recorded_at desc limit 1"` shows fresh row.

**Fallback** (only if Anthropic /usage endpoint refuses OAuth bearer): edit `systemd/claude-soma-usage-snapshot.timer` back to `OnCalendar=*-*-* 23:55:00`; emit NEEDS_INPUT in morning report explaining the fallback.

### W1D — T2 + L5 fixes (MEDIUM, LOW): daily_status budget tighten + rc_url_refresh stricter busy heuristic

**Leak hypothesis (T2):** `scripts/daily_status.sh` enrichment prompt includes 25 lines of pane tail per lead + 8 git commits + 1200 chars per NEXT/MEMORY = 5–10k tokens once daily.

**Leak hypothesis (L5):** `scripts/rc_url_refresh.py:134–139` busy-heuristic is a last-line check; misfire injects keystrokes mid-turn → could submit empty/malformed input → one wasted ~$12.66 round-trip on a 844k-context lead.

**Files to modify:**
- `scripts/daily_status.sh`
- `scripts/rc_url_refresh.py`

**Fix (T2):** reduce pane tail lines from 25 → 10 per lead; reduce git log from 8 → 4 commits; trim NEXT/MEMORY notes to 600 chars. Target enrichment prompt under 3k tokens total.

**Fix (L5):** tighten `_is_busy(pane)` — require the pane's last non-empty line to match the EXACT idle prompt regex (e.g. `r'^\$ $'` or whatever the active sentinel is). If ambiguous, skip the keystroke injection. Add a log line on every skip with reason.

**Acceptance test:** existing tests still green. For T2, manually inspect prompt size via `wc -c` after running script in dry-run mode (add `--dry-run` flag if needed). For L5, add unit test covering: idle pane → injects; ambiguous pane → skips; busy pane → skips.

### W1E — L4 fix (HIGH): channel /clear weekly cron + healthcheck nudge

**Leak hypothesis:** `scripts/healthcheck.sh:102–135` respawns `channel-claude.sh` which runs `claude --continue` reloading the channel's 269k-token transcript on every restart. With healthcheck every 10 min and flapping bun, this compounds.

**Files to modify:**
- `scripts/healthcheck.sh`
- New: `scripts/channel_clear.sh` (Sunday weekly cron entry point)
- New: `systemd/claude-soma-channel-clear.service` + `.timer`

**Fix:** new systemd timer `claude-soma-channel-clear.timer` fires Sundays 03:00 UTC; ExecStart runs `scripts/channel_clear.sh` which `tmux send-keys -t hermes:bot '/clear' Enter` then waits 3s for confirmation. Edit `healthcheck.sh` to add a log warning (not action) when channel session age > 7 days, prompting operator to inspect.

**Acceptance test:** `bash -n` syntax check; `systemd-analyze verify` for the new unit; dry-run `channel_clear.sh` against a mock tmux socket asserts send-keys was called.

### W1F — F1 fix (MEDIUM): routines run_routine FileLock

**Leak hypothesis:** `src/claude_soma/api/routes/routines.py:449–453` `run_routine` POST endpoint calls `_call_claude_routines("run", ...)` with default 120s timeout AND no FileLock guard. The list path is FileLocked since `ee6ca29` but the run path is not.

**Files to modify:**
- `src/claude_soma/api/routes/routines.py`
- `tests/api/test_routines_locking.py` (extend)

**Fix:** wrap the inner call inside `run_routine` with `FileLock("/tmp/hermes-routines-run.lock", timeout=60)`. Cap the `_call_claude_routines` timeout to 30s (match the list-path budget; pass via existing `claude_timeout` kwarg if it exists, else add it).

**Acceptance test:** extend `test_routines_locking.py` with a concurrent-POST test similar to the existing list test: spawn 5 threads hitting `run_routine` concurrently; mock `subprocess.run`; assert max-concurrent-subprocess-call-count == 1.

**Live verification:** after deploy, `ab -n 10 -c 10 -m POST http://127.0.0.1:8000/api/routines/test/run`; `journalctl -u claude-soma-api -f` should show serialized claude-p invocations.

### W1G — T3 fix (MEDIUM): hermes_api SO_REUSEADDR + prewarm debounce

**Leak hypothesis:** `src/claude_soma/mcp_servers/hermes_api/server.py:904–920` spawns `_prewarm_routines_cache` daemon at startup which calls `claude -p` via the routines cached query. The port-9100 EADDRINUSE restart loop (per TROUBLESHOOTING_FINDINGS.md §3) causes each restart to fire one `claude -p`. If the loop is 2s/restart for 5 min → 150 claude calls.

**Files to modify:**
- `src/claude_soma/mcp_servers/hermes_api/server.py`
- `tests/mcp_servers/test_hermes_api_listener.py` (extend or create)

**Fix (a):** add `SO_REUSEADDR` (and/or check-port-and-skip-if-busy) to the notify listener socket bind at the `_start_notify_listener` thread. This breaks the restart loop.

**Fix (b):** add startup debounce to `_prewarm_routines_cache` — read a marker file `/tmp/hermes-prewarm-last.ts`; if last warm was within 300s, skip. Update marker on each warm.

**Acceptance test:** mock socket bind to raise EADDRINUSE once → assert server retries with SO_REUSEADDR and binds successfully. For debounce: mock marker file to be recent → assert `_query_cloud_routines_cached` is NOT called.

**Live verification:** `sudo systemctl restart claude-soma-api && sleep 1 && sudo systemctl restart claude-soma-api` → no port conflict; `ps auxf | grep claude` shows no extra claude-p invocations from the second restart.

### W1H — L1 + F2 + L2 fixes (CRITICAL + HIGH): resume hardening bundle

**Leak hypothesis (L1+F2):** `src/claude_soma/mcp_servers/project_orchestrator/server.py:207` `resume_project_impl` skips BOTH `_check_safety_gate()` AND the `_reconcile_active()` MAX_CONCURRENT cap that `spawn_project_impl` enforces. A quota-exhausted lead at 844k context resumed = ~$12.66/turn no check.

**Leak hypothesis (L2):** `src/claude_soma/mcp_servers/project_orchestrator/spawner.py:430–447` `resume_background_lead` passes `--resume <session_uuid>` which re-uploads the FULL cloud transcript on every resume. No context-size guard.

**Files to modify:**
- `src/claude_soma/mcp_servers/project_orchestrator/server.py`
- `src/claude_soma/mcp_servers/project_orchestrator/spawner.py`
- `tests/mcp_servers/test_project_orchestrator.py` (extend)
- `tests/mcp_servers/test_orchestrator_spawner.py` (extend)

**Constraint:** do NOT touch `src/claude_soma/mcp_servers/project_orchestrator/registry.py` — that file is reserved for Wave 2 (L3). Read existing registry methods; do not add new ones.

**Fix (L1+F2):** at the top of `resume_project_impl`, add:
```python
_check_safety_gate()
active = _reconcile_active()
if len(active) >= MAX_CONCURRENT:
    raise RuntimeError(f"concurrency cap ({MAX_CONCURRENT}) reached; kill a lead first")
```
Mirror exactly what `spawn_project_impl:99-108` does.

**Fix (L2):** in `spawner.py`, add a context-size estimator helper:
```python
def _estimate_context_tokens(name: str) -> int:
    """Rough estimate from registry lead_events payload bytes ÷ 4."""
    # SUM(LENGTH(payload_json)) FROM lead_events WHERE lead = ?
    # Divide by 4 for chars→tokens approximation.
```
Then inside `resume_background_lead`, before constructing the `claude --resume ...` argv:
```python
est_tokens = _estimate_context_tokens(name)
threshold = int(os.environ.get("HERMES_RESUME_CONTEXT_GUARD_TOKENS", "200000"))
if est_tokens > threshold and not force:
    raise RuntimeError(
        f"context guard: {name} estimated at {est_tokens} tokens > {threshold}; "
        "kill + re-spawn fresh, or pass force=True to override"
    )
```
Add a `force: bool = False` param to `resume_background_lead` (default False); thread through from `resume_project_impl`'s signature.

**Acceptance tests (new):**
- `test_resume_project_quota_gate_raises`: mock `_check_safety_gate` to raise → `resume_project_impl` propagates.
- `test_resume_project_concurrency_cap_raises`: mock `_reconcile_active` to return MAX_CONCURRENT items → resume raises.
- `test_resume_context_guard_raises_above_threshold`: insert lead_events with payload totaling > 200k×4 bytes → `resume_background_lead` raises.
- `test_resume_context_guard_force_overrides`: same setup + `force=True` → succeeds.

### W2 — L3 fix (HIGH): per-lead turn counter + reaper auto-kill

**Leak hypothesis:** `scripts/reaper.py:56–68` hibernates leads at 24h IDLE only. A stuck working lead (wan-manager pattern, ZeroGPU quota-retry every few minutes) touches `last_activity` on every tool call and never crosses the idle threshold. No turn count, no context-size budget. 29h+ burn.

**Files to modify:**
- `src/claude_soma/mcp_servers/project_orchestrator/registry.py` (schema migration + new methods)
- `scripts/reaper.py` (new kill path)
- `tests/mcp_servers/test_project_orchestrator_registry.py` (extend)
- `tests/scripts/test_reaper.py` (extend)

**Fix (registry):**
- Add migration: `ALTER TABLE projects ADD COLUMN turn_count INTEGER NOT NULL DEFAULT 0;` Guarded by the existing migration mechanism (check column existence; idempotent).
- Add `Registry.increment_turn_count(name: str) -> None` — bumps by 1.
- Add `Registry.get_turn_count(name: str) -> int`.
- Add `Registry.reset_turn_count(name: str) -> None` — called on COMPLETED event from FI-NOTIFY listener.
- Wire `increment_turn_count` into the FI-NOTIFY event store: every `STARTED` event = +1 turn (rough proxy; user can refine).

**Fix (reaper):** in `scripts/reaper.py`, after the existing idle check, add:
```python
turn_cap = int(os.environ.get("HERMES_LEAD_TURN_CAP", "50"))
ctx_cap = int(os.environ.get("HERMES_LEAD_CONTEXT_CAP_TOKENS", "200000"))
if turn_count >= turn_cap or est_tokens >= ctx_cap:
    kill_and_hibernate(name)
    fire_needs_input(lead=name, reason=f"auto-killed at turn={turn_count} ctx={est_tokens}")
```
The `fire_needs_input` uses the existing direct EventStore.insert_event + POST /notify pattern.

**Acceptance tests (new):**
- `test_registry_turn_count_migration_idempotent`: run migration twice → no error.
- `test_registry_increment_resets`: increment 3 times → 3; reset → 0.
- `test_reaper_kills_on_turn_cap`: set turn_count=51, last_activity=now → reaper kills.
- `test_reaper_kills_on_context_cap`: insert lead_events totaling 200k+ tokens → reaper kills.

### W3 — ME-1 (HIGH): Telegram DM alarm worker on high-context leads

**Leak hypothesis:** the prior soma-improver session at 844k context burned ~$12/turn unattended. No active surface alerted the operator before the quota drained overnight. A Telegram DM at >150k estimated context gives a hard real-time signal.

**Files to modify:**
- `src/claude_soma/mcp_servers/hermes_api/alarm_worker.py` (NEW)
- `src/claude_soma/mcp_servers/hermes_api/server.py` (wire new daemon thread)
- `tests/mcp_servers/test_alarm_worker.py` (NEW)

**Constraint:** the alarm worker reads `turn_count` and estimates context from registry — both shipped by Wave 2. ME-1 is a strict consumer.

**Fix:** new daemon thread `_run_alarm_worker` started in `hermes_api/server.py:main()`. Polls every 10 minutes:
```python
for project in registry.list_active():
    est_tokens = registry.estimate_context_tokens(project["name"])
    if est_tokens >= 150_000:
        last_alarm_ts = _alarm_state.get(project["name"], 0)
        if time.time() - last_alarm_ts > 3600:  # 1h debounce per lead
            send_telegram_dm(
                f"⚠️ Lead {project['name']} at ~{est_tokens} tokens "
                f"({turn_count} turns) — consider /clear or fresh spawn before next turn."
            )
            _alarm_state[project["name"]] = time.time()
```
Uses the existing `send_telegram_dm` helper (same path as `healthcheck.sh` NEEDS_REAUTH). Threshold env-knob: `HERMES_ALARM_CONTEXT_THRESHOLD_TOKENS` (default 150000), `HERMES_ALARM_DEBOUNCE_SECONDS` (default 3600).

**Acceptance test:** mock registry to return a lead with est_tokens=160000; mock telegram send; advance time → assert send called once; advance time by 30 min → assert send NOT called (debounced); advance by 1h → assert send called again.

**Frontend deferral:** dashboard banner for the same signal is out of scope this round (open follow-up). DM is the hard signal.

---

## Part 6 — Risk-tiered ordering inside each wave

Within Wave 1 (all parallel, no inter-dispatch dep), the order they FINISH doesn't matter — but the order I FAN OUT does for resource pacing. Plan:

1. **Dispatch order (all simultaneous in one tool-use block):** W1A, W1B, W1C, W1D, W1E, W1F, W1G, W1H — single message, 8 Agent() calls. Parallel.
2. **Verification order (sequential as each finishes):** as task-notifications arrive, verify per-subagent (read diff + commit msg + tests pass), accumulate the "ready to restart" list.
3. **Restart trigger:** only after ALL 8 subagents report success. If any STOP-AND-SURFACE → triage: if the failed subagent doesn't block restart of the others (script-only), proceed with partial Wave 1 restart and re-dispatch the failed one as W1X-v2; if the failed subagent is W1H (resume) — defer restart and STOP-AND-SURFACE for morning.

Wave 2 is sequential to Wave 1 restart. Wave 3 is sequential to Wave 2 restart.

---

## Part 7 — Acceptance criteria per wave

### Wave 1 acceptance
- 8 commits on `origin/main` (or fewer if any STOP'd), each authored by `Mayank Gupta <techfreakworm@gmail.com>`, no Co-Authored-By.
- Combined pytest pass: `pytest tests/` exits 0 (current baseline: 697 pass + 1 skip per S-EVENT-HANDLING report). The new tests bring the total higher; no regressions.
- /opt deploy: `git -C /opt/claude-soma pull --ff-only` succeeds; no rebase conflicts; no force-push.
- Restart: `claude-soma-channel.service` + `claude-soma-api.service` `ActiveEnterTimestamp` post the RESTART REQUIRED MILESTONE event timestamp.
- Live H1 check: insert a synthetic MILESTONE with progress containing "RESTART REQUIRED test_h1_verify"; trigger UserPromptSubmit by sending a Telegram message; assert sqlite `hook_injected_at` row populated within 5s.
- Live T1 check: `systemctl start claude-soma-usage-snapshot.service`; `ps auxf | grep claude` shows no new claude process; `usage.sqlite` last row updated.
- Live F1 check: `ab -n 5 -c 5 -m POST http://127.0.0.1:8000/api/routines/<test_id>/run` (or curl loop); assert max-concurrent-claude-processes == 1 via `ps`.
- Live W1H check: try `resume_project` on a non-existent quota-exhausted lead → expect RuntimeError surfacing the gate. Try with fake high-context registry → expect context-guard RuntimeError.

### Wave 2 acceptance
- 1 commit on origin/main; pytest pass.
- /opt deploy + migration verification: `sqlite3 /opt/claude-soma/registry.sqlite ".schema projects" | grep turn_count` returns a hit.
- Live: simulate a registry row with `turn_count=51` and recent `last_activity`; run reaper manually (`/opt/claude-soma/.venv/bin/python /opt/claude-soma/scripts/reaper.py`); assert lead is hibernated + a NEEDS_INPUT event lands in `lead_events`.

### Wave 3 acceptance
- 1 commit on origin/main; pytest pass.
- /opt deploy.
- Live: insert a fake lead with simulated >150k estimated context; restart claude-soma-api; within 10 min of polling, assert a Telegram DM is delivered (verify via journalctl tail of the api service showing `alarm_worker: sent DM for <name>`).

---

## Part 8 — Token-leak metric to monitor

**Primary signal post-Wave 1:** spawn `ps auxf | grep "claude " | wc -l` on a 1-min cron for the auto-restart window. Pre-fix baseline: occasional spikes to 5+ during the routines thunder-herd + 96/day from usage_snapshot. Post-fix expected: 0–1 baseline; spikes only during real spawn/resume operations.

**Secondary signal:** `/api/usage` snapshot row delta from one period to the next (post-T1 → 15-min granularity gives spike-visibility within 30 min vs prior 24h).

**Auto-restart loop signal (H1):** count `auto-restart-services.sh` invocations per `HERMES_AUTO_RESTART_WINDOW_UTC` window. Pre-fix: up to 20/hr. Post-fix: ≤1 per RESTART REQUIRED MILESTONE event.

---

## Part 9 — Risk + rollback notes

- **Worst case during overnight execution:** a Wave 1 subagent ships broken code, channel restart loops on import error. Mitigation: Wave 1H is the only one that touches importable Python that the channel loads at startup; its tests cover the new code. If channel fails to come up after restart, the auto-restart helper's safety regex won't fire repeatedly within the same window (already shipped) — the channel just stays dead until morning. The user reads in the COMPLETED report that channel is down + the failing commit SHA. Rollback: `git revert <sha>` + push + auto-restart.
- **L3 schema migration risk:** ADD COLUMN with DEFAULT 0 is non-blocking on SQLite. Existing rows get the default. Rollback if needed: don't drop the column — just stop using it.
- **L2 `--resume` guard false-positive:** the estimator is rough (chars/4). A legitimately small-but-old lead with many short events could trip the guard. Mitigation: the error message instructs `force=True` override.
- **T1 OAuth /usage fallback:** if Anthropic /usage endpoint rejects OAuth bearer (uncertain), the subagent's fallback path is to revert the timer to daily. This is captured in the brief.

---

## Part 10 — What user reads in the morning

The COMPLETED FI-NOTIFY event (and a dump emitted to this file's "Execution log" section appended by the lead after each wave) will contain:

- Per-candidate status: SHIPPED / STOP-AND-SURFACE'd / DEFERRED.
- Per-wave commit SHAs + diffstats.
- Restart timestamps (channel + api ActiveEnterTimestamps) post-MILESTONE.
- Live verification check results per candidate.
- Any NEW leak surfaced as candidate #14+.
- Prompt-token rate observation if collectible.
- Open follow-ups (e.g. dashboard banner for ME-1; any STOP-AND-SURFACE briefs at `/tmp/<id>-STOP.md`).

---

## Execution log (appended by lead after each wave)

_(will be filled in during execution)_

