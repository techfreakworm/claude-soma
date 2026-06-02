# PLAN-ADMIN-FIXES.md

**Status:** DRAFT — awaiting user approval. Sleep-mode end-to-end was for the prior overnight push only; this round expects per-wave or batched approval.

**Generated:** 2026-06-02 by soma-improver. Methodology: 4 opus+max+sequential-thinking PLANNING subagents (S1 admin tabs / S2 zero-LLM audit / S3 infra / S4 lifecycle) running in parallel; synthesized into a single coherent plan. Per-subagent reports archived at `/tmp/PAF-S{1,2,3,4}-*.md` on the lead host.

**Scope:** All three buckets from the user's brief:
- **A** — 5 broken admin portal tabs (Usage, Broadcast, Projects DM, Conversations, Memory)
- **B** — Zero-LLM-cost audit of every admin endpoint; every offender on page-load path replaced with pure Python/SQL/file-read
- **C** — Pending issues: markserv systemd, frontend deploy cp, FI-GATE V3, grok-image binary path, codex timeout hard-kill, FI-NOTIFY listener-direct auto-restart

**Out of scope:** anything in `FAR-FETCHED.md`. Soma-improver pane /clear is operator-driven (per user note in brief).

---

## Headline status

| Item | Bucket | Status | Owner subagent | Restart impact |
|---|---|---|---|---|
| A1 Usage tab always 0 | A | FIX REQUIRED | W1A | none (timer-fired script) |
| A2 Broadcast not working | A | FIX REQUIRED | W1B | api |
| A3 Projects DM stub | A | FIX REQUIRED | W1C | channel |
| A4 Conversations always empty | A | FIX REQUIRED | W1D (bundled w/ A5) | channel (MCP loaded by channel) |
| A5 Memory no stats | A | FIX REQUIRED | W1D (bundled w/ A4) | channel + api + frontend rebuild |
| B LLM page-load offender (`GET /routines`) | B | FIX REQUIRED | W1E (bundled w/ FI-NOTIFY) | api + channel |
| B prewarm amplifier (`hermes_api:945`) | B | FIX REQUIRED | W1E | channel |
| C markserv systemd | C | FIX REQUIRED | W1F | new systemd svc + daemon-reload |
| C frontend `cp -r .next/static` | C | **ALREADY SHIPPED** (build_frontend.sh:30-33, commits `27e5f8b`/`7f7b729`) — notify-only | — | — |
| C FI-GATE V3 | C | FIX REQUIRED | W1G | none (hook re-read) |
| C grok-image binary path | C | **ALREADY SHIPPED** (commit `eb31cc9`, sole call site) — notify-only | — | — |
| C codex timeout hard-kill | C | FIX REQUIRED | W1H | none (read at lead spawn) |
| C FI-NOTIFY listener-direct | C | FIX REQUIRED | W1E (bundled w/ B) | channel |

**Tally:** 10 code-fix candidates across 8 Wave-1 subagents + 2 notify-only.

---

## Part 1 — Dependency DAG

```
                     ┌────────────────────────────────────────────────────┐
                     │ WAVE 1 — independent surfaces (8 sonnet subagents) │
                     ├────────────────────────────────────────────────────┤
                     │ W1A: T-USAGE   usage_snapshot.py JSONL scan        │
                     │ W1B: T-BROADCAST admin.py Telegram direct POST     │
                     │ W1C: T-PROJDM  project_orchestrator tmux send-keys │
                     │ W1D: T-CONV+T-MEM  claude_state + memory + FE page │
                     │ W1E: LLM-ROUTINES + L-FINOTIFY  hermes_api bundle  │
                     │ W1F: I-MARKSERV  launch.sh + .service realign      │
                     │ W1G: I-GATE-V3  Python shlex parser + shim         │
                     │ W1H: L-CODEX   responsive_bot.md + SKILL.md        │
                     └──────────────┬─────────────────────────────────────┘
                                    │
                channel + api + frontend rebuild + markserv enable
                                    │
                                    ▼
                            Live verification
                                    │
                                    ▼
                              COMPLETED report
```

**Why one wave:** every Wave-1 candidate is file-disjoint after bundling A4+A5 (shared `claude_state.py`) and B+FI-NOTIFY (shared `hermes_api/server.py`). No registry schema migration this round, so no Wave 2 forced. The two bundles deliberately serialize work that touches the same file inside a single subagent to avoid rebase conflicts.

---

## Part 2 — Restart matrix

| Service / artifact | Touched by | Action |
|---|---|---|
| `claude-soma-channel.service` | W1C (project_orchestrator MCP), W1D (hermes_api MCP loaded by channel), W1E (hermes_api MCP) | restart |
| `claude-soma-api.service` | W1B (admin.py), W1D (memory.py), W1E (routines.py) | restart |
| Frontend (Next.js dashboard) | W1D (frontend/app/admin/memory/page.tsx) | `bash scripts/build_frontend.sh` then `systemctl restart claude-soma-frontend.service` |
| `claude-soma-markserv.service` (NEW) | W1F | `cp` to /etc + `daemon-reload` + `enable --now` |
| systemd `daemon-reload` | W1F (new svc), W1A (none — script self-contained, no timer change) | once |
| Hooks (`notify_inject.sh`, `orchestrator_gate.sh/.py`) | W1G, W1E | none — re-read per fire |
| Lead system prompt + skill (`responsive_bot.md`, `codex-image-gen/SKILL.md`) | W1H | none — read at lead spawn |

**Auto-restart trigger note:** W1E ships the FI-NOTIFY listener-direct path. After it lands + channel restart, future `RESTART REQUIRED MILESTONE` events trigger restart on the WRITE path (not UserPromptSubmit). For THIS wave the restart still relies on the overnight bash poller OR an operator `sudo systemctl restart` — because W1E itself adds the new auto-trigger that won't be live until after the very restart it's part of.

---

## Part 3 — Coordination protocol for shared-file touchers

| File | Touched by |
|---|---|
| `scripts/usage_snapshot.py` | W1A only |
| `src/claude_soma/api/routes/admin.py` | W1B only |
| `src/claude_soma/mcp_servers/project_orchestrator/server.py` | W1C only (line 154 `send_to_project_impl`) |
| `src/claude_soma/mcp_servers/hermes_api/claude_state.py` | W1D only (lines 65, 75, 95) |
| `src/claude_soma/api/routes/memory.py` | W1D only |
| `frontend/app/admin/memory/page.tsx` | W1D only |
| `src/claude_soma/api/routes/routines.py` | W1E only (drop cloud submission at line 430) |
| `src/claude_soma/mcp_servers/hermes_api/server.py` | W1E only — TWO disjoint regions: prewarm at ~945, new `_maybe_trigger_auto_restart` near 632 + wire inside `_handle_notify` at 798-822 |
| `src/claude_soma/mcp_servers/hermes_api/notify_store.py` | W1E only (schema migration + `claim_auto_restart`) |
| `scripts/notify_inject.sh` | W1E only (add jq filter for `auto_restart_fired_at`) |
| `scripts/markserv-launch.sh` | W1F only |
| `systemd/claude-soma-markserv.service` | W1F only |
| `scripts/orchestrator_gate.sh` | W1G only (now a thin shim) |
| `scripts/orchestrator_gate.py` (NEW) | W1G only |
| `system_prompts/responsive_bot.md` | W1H only |
| `skills/codex-image-gen/SKILL.md` | W1H only |

**Git hygiene (binding):** every subagent stages SPECIFIC paths only — no `git add -A`, no `git add .`. Push to `origin/main` directly. On non-fast-forward: rebase, no force. On rebase conflict: STOP-AND-SURFACE to `/tmp/<TASK-ID>-STOP.md` and exit non-zero.

**Author convention:** `Mayank Gupta <techfreakworm@gmail.com>` sole author. NO `Co-Authored-By`, NO Claude footer, NO emoji in commit messages or code.

**Worktree isolation:** each subagent runs with `isolation: worktree` to avoid shared-working-tree races (matching the W4 + leak-fix pattern).

---

## Part 4 — Pre-decided defaults (the 5 open questions)

These five had multiple sound options. Defaults chosen for the planning recommendation; the user is asked to confirm or override **before approving Wave 1 dispatch**:

1. **Routines tab Option A vs B.** Default: **Option A — drop the cloud query entirely on page-load.** Routines list renders registry+systemctl+cron only; cloud rows show `last_run/next_run` as `"—"` until something else writes them. Eliminates the LLM call on every dashboard load AND removes the prewarm thread amplifier in one stroke. Option B (background timer refresh every 1h) deferred as follow-up.
2. **Broadcast Option A vs B.** Default: **Option A — direct Telegram Bot API POST inside the handler.** No new daemon; immediate visible result; matches existing `healthcheck.sh` Telegram pattern for NEEDS_REAUTH.
3. **Memory response shape.** Default: include `path` in response (debug aid; operator visible). Frontend renders 5 stats cards: Size / Lines / Headings / Sections / Modified.
4. **Usage interactive-vs-agent_sdk split heuristic.** Default: **service_tier-based** per the S1 recommendation (`batch`/`priority` → agent_sdk; other → interactive). Simpler than directory-based; matches Claude billing tiers; can be refined.
5. **FI-GATE V3 `$(...)` substitution handling.** Default: **fail-OPEN** with an `activity.jsonl` counter. After observing zero false-positives over 24-48h, tighten to deny. (S3 recommended this; conservative.)

If the user wants a different default on any, say so before W1 dispatch.

---

## Part 5 — Per-candidate subagent briefs (paste-ready)

All briefs share this preamble (binding constraints):

> You are a sonnet+max+sequential-thinking implementation subagent. Use `mcp__sequential-thinking__sequentialthinking` with `--effort max` BEFORE writing code. Work in your fresh git worktree (the harness has set you up off `origin/main`). Read the listed files BEFORE writing. Stage SPECIFIC paths only — never `git add -A`. Commit with `Author: Mayank Gupta <techfreakworm@gmail.com>` (no `Co-Authored-By`, no Claude footer, no emoji). Push as `git push origin HEAD:main`. On non-fast-forward: rebase, re-push. On conflict: STOP-AND-SURFACE to `/tmp/<TASK-ID>-STOP.md` and exit non-zero. Report commit SHA + diffstat + pytest result.

### W1A — T-USAGE: usage_snapshot.py JSONL scan (replaces `claude -p /usage`)

**Task ID:** PAF-W1A-USAGE
**Leak:** `scripts/usage_snapshot.py:37` shells `claude -p '/usage' --output-format json` whose output has none of the expected `interactive_credits_used`/`agent_sdk_credits_used` keys, so every `daily_snapshots` row is 0.0. Plus the call itself burns one Claude turn per fire even though no inference is needed.

**Files to modify (EXACT):**
- `scripts/usage_snapshot.py`
- `tests/scripts/test_usage_snapshot.py`

**Fix:** rewrite `_query_usage()` + `_extract()` as a pure file-scanner over `~/.claude/projects/*/*.jsonl`. For every JSON line with both `"usage"` and `"timestamp"`, parse, filter by today's UTC date, sum `input_tokens + output_tokens + cache_creation_input_tokens` into `iu` (interactive) vs `au` (agent_sdk) by `message.usage.service_tier` (`batch`/`priority` → agent_sdk; other → interactive). Ceilings read from `HERMES_INTERACTIVE_CEILING` + `HERMES_AGENT_SDK_CEILING` env (default 0 = unknown). Drop `subprocess` import. Keep the existing sqlite writer unchanged.

**Acceptance test:** mock `Path.home()` to a tempdir containing two synthetic JSONL files (one batch-tier line, one standard-tier line, both with today's timestamp); assert `subprocess.run` NOT called; assert the daily_snapshots row reflects the summed token counts.

**Live verification (post-deploy):** `systemctl start claude-soma-usage-snapshot.service`; `ps auxf | grep claude` shows no new claude; `sqlite3 /opt/claude-soma/usage.sqlite "SELECT * FROM daily_snapshots ORDER BY recorded_at DESC LIMIT 1"` shows non-zero values.

### W1B — T-BROADCAST: admin.py direct Telegram POST

**Task ID:** PAF-W1B-BROADCAST
**Leak:** `src/claude_soma/api/routes/admin.py:25` appends to `/opt/claude-soma/broadcast.jsonl` but no consumer exists ("Task 36" never shipped). User sees "queued" but nothing reaches Telegram.

**Files to modify (EXACT):**
- `src/claude_soma/api/routes/admin.py`
- `tests/api/test_admin_routes.py` (extend or create)

**Fix:** in the broadcast handler, AFTER the existing file append, also POST `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage` with `chat_id=${TELEGRAM_CHAT_ID}` (or `HERMES_NOTIFY_CHAT_ID` fallback) and `text=body.message` via `urllib.request`. 10s timeout. Return `{"queued_at": ts, "delivered": bool, "error": str|None}`. Add the env vars to `systemd/claude-soma-api.service`'s `EnvironmentFile=/etc/claude-soma/secrets.env` if not already loaded.

**Acceptance test:** mock `urllib.request.urlopen` to return 200; POST `/api/admin/broadcast` with a test message; assert response has `delivered=True` AND the file got appended. Second test: mock urlopen to raise; assert `delivered=False` AND `error` populated.

**Live verification:** click "Send broadcast" in the dashboard with a test message; verify the Telegram channel receives it within 2s.

### W1C — T-PROJDM: project_orchestrator tmux send-keys

**Task ID:** PAF-W1C-PROJDM
**Leak:** `src/claude_soma/mcp_servers/project_orchestrator/server.py:154` `send_to_project_impl` only `touch`es the registry; the `message` arg is silently dropped.

**Files to modify (EXACT):**
- `src/claude_soma/mcp_servers/project_orchestrator/server.py`
- `tests/mcp_servers/test_project_orchestrator.py`

**Fix:** rewrite `send_to_project_impl(name, message)` to actually deliver via `tmux send-keys`. Use the `LEAD_SOCKET_PREFIX="soma-lead-"` + `TMUX_SESSION_PREFIX="soma-proj-"` from `spawner.py`. Two-step: `tmux -L soma-lead-<name> send-keys -t soma-proj-<name> -l <message>` (literal) then `tmux -L ... send-keys -t ... Enter`. Mirrors `scripts/rc_url_refresh.py:104-110`. Guard with `is_lead_alive(name)`; raise RuntimeError if not. Wrap subprocess calls in try/except CalledProcessError, TimeoutExpired (10s each) per the repo subprocess convention. Add `"delivered": True` to the response on success.

**Acceptance test:** mock `is_lead_alive` to True; mock `subprocess.run`; call the impl; assert two subprocess.run calls in order (the `-l message` call then the `Enter` call) with the correct socket and session names; assert response has `delivered: True`. Second test: mock `is_lead_alive` to False → assert RuntimeError raised.

**Live verification:** open Projects tab in dashboard, type a message into a project's DM box, click Send; verify the message lands in the lead's tmux pane within 1s.

### W1D — T-CONV + T-MEM: claude_state.py + memory.py + frontend stats panel

**Task ID:** PAF-W1D-CONV-MEM
**Leak 1 (T-CONV):** `src/claude_soma/mcp_servers/hermes_api/claude_state.py:75` globs `<proj>/transcripts/*.jsonl` but transcripts live at `<proj>/*.jsonl` directly. Same wrong path at line 95 in `read_transcript`.
**Leak 2 (T-MEM):** `claude_state.py:65` `read_memory(project_slug)` uses raw project name as the dir slug, but Claude Code dir names are dash-encoded cwds (e.g. `-home-ubuntu-projects-mayank-portfolio`). Plus the frontend renders no stats.

**Files to modify (EXACT):**
- `src/claude_soma/mcp_servers/hermes_api/claude_state.py`
- `src/claude_soma/api/routes/memory.py`
- `frontend/app/admin/memory/page.tsx`
- `tests/mcp_servers/test_claude_state.py` (extend or create)

**Fix (T-CONV):** in `list_transcript_threads()`, drop the `transcripts/` subdir from the glob; iterate `proj_dir.glob("*.jsonl")` directly. Update the returned `project` field to `f.parent.name` (one level less). In `read_transcript()`, drop `/transcripts/` from the path construction. Preserve the existing root-relative-path guard.

**Fix (T-MEM):** rewrite `read_memory(project_slug, cwd: str | None = None)` to return a dict `{project, text, stats}` with `stats` containing `bytes, lines, chars, sections, headings, last_modified, path`. Resolve the on-disk directory via several candidates in priority order:
1. If `cwd` provided: `root / cwd.replace("/", "-") / "memory" / "MEMORY.md"`
2. Raw slug: `root / project_slug / "memory" / "MEMORY.md"`
3. If slug doesn't start with `-`: `root / f"-home-ubuntu-projects-{project_slug}" / "memory" / "MEMORY.md"`
4. If slug == `default`: `root / "-home-ubuntu" / "memory" / "MEMORY.md"`

Each candidate must `path.resolve().is_relative_to(root.resolve())`. Return the first existing match.

Update `src/claude_soma/api/routes/memory.py` to look up `cwd` from `Registry().get(project)` (defensive try/except) before calling `read_memory(project, cwd=cwd)`.

Update `frontend/app/admin/memory/page.tsx` to add 5 `KpiCard` tiles above the text body: Size (KB), Lines, Headings, Sections, Modified. Type the response with the new `stats` field. Render `"—"` if `stats.last_modified == 0`.

**Acceptance test:** mock `_projects_root()` to a tempdir with `~/-home-ubuntu-projects-fake/memory/MEMORY.md` containing `# H1\n## S1\nbody\n## S2\n`. Call `read_memory("fake", cwd="/home/ubuntu/projects/fake")` → assert text matches, `stats.headings == 1`, `stats.sections == 2`, `stats.bytes > 0`. Second test: `list_transcript_threads()` against a tempdir with `~/-fake-proj/abc.jsonl` and `~/-fake-proj/def.jsonl` returns 2 entries sorted by mtime desc.

**Live verification:** Conversations tab — should show transcripts from active projects. Memory tab — click each project pill; should show the MEMORY.md content plus the 5 stats cards.

### W1E — LLM-ROUTINES + L-FINOTIFY (BUNDLED): routines.py + hermes_api/server.py + notify_store.py + notify_inject.sh

**Task ID:** PAF-W1E-LLM-FINOTIFY (CRITICAL — biggest token-cost surface + listener-direct safety upgrade)

This is the largest single subagent in the wave. Bundled because both fixes touch `src/claude_soma/mcp_servers/hermes_api/server.py` (disjoint regions but same file). Use sequential-thinking to plan the order: do LLM-ROUTINES first (deletes the prewarm thread + drops cloud submission), then L-FINOTIFY (adds new `_maybe_trigger_auto_restart` + wires it into `_handle_notify`).

**Leak 1 (LLM-ROUTINES):** `src/claude_soma/api/routes/routines.py:430` (`list_routines`) calls `_query_cloud_routines_cached()` which shells `claude -p`. Every dashboard load of `/admin/routines` blocks SSR on this. Cache TTL 300s masks but does not eliminate cost. Plus `src/claude_soma/mcp_servers/hermes_api/server.py:945-946` spawns `_prewarm_routines_cache` on every hermes_api startup — same `claude -p`.

**Leak 2 (L-FINOTIFY):** the auto-restart trigger today fires via `notify_inject.sh` (UserPromptSubmit only) + the overnight bash poller (operator band-aid, not in git). Both wrong shape. Proper fix: the `_NotifyHandler._handle_notify` in `hermes_api/server.py:798-822` should invoke `auto-restart-services.sh` DIRECTLY on event insert when payload matches `RESTART REQUIRED ... (services: ...)` and `HERMES_AUTO_RESTART_WINDOW_UTC` is set + unexpired.

**Files to modify (EXACT):**
- `src/claude_soma/api/routes/routines.py`
- `src/claude_soma/mcp_servers/hermes_api/server.py` (two regions: ~632 add helper + ~798 wire + ~945 delete prewarm thread)
- `src/claude_soma/mcp_servers/hermes_api/notify_store.py` (schema + new method)
- `scripts/notify_inject.sh` (jq filter for `auto_restart_fired_at`)
- `tests/api/test_routines.py` (extend)
- `tests/mcp_servers/test_hermes_api_listener.py` (extend or new)
- `tests/mcp_servers/test_notify_store.py` (extend)

**Fix (LLM-ROUTINES — Option A from S2 audit):**
1. In `routines.py:list_routines()`, REMOVE the cloud query thread-pool submission. Build the merged list from registry + systemctl + cron only. Gate behind env knob `HERMES_ROUTINES_CLOUD=off` (default off — meaning cloud rows pull from registry; setting it to `on` re-enables the cloud query for users who explicitly want it).
2. In `hermes_api/server.py:945-946`, DELETE the `t_prewarm = threading.Thread(target=_prewarm_routines_cache, daemon=True)` spawn and the line that starts it. Leave the function itself in place (unused; can be removed in a follow-up). The W1G T3 prewarm-debounce code is no longer needed once the thread is gone — leave it in (defensive).

**Fix (L-FINOTIFY):**
1. In `notify_store.py`: add `auto_restart_fired_at REAL` to the `lead_events` `CREATE TABLE` in `_SCHEMA`. Add a defensive `ALTER TABLE` migration in `EventStore.__init__` (catch `sqlite3.OperationalError` → already-migrated). Add method:
   ```python
   def claim_auto_restart(self, event_id: int) -> bool:
       with self._lock:
           cur = self._conn.execute(
               "UPDATE lead_events SET auto_restart_fired_at = ? "
               "WHERE id = ? AND auto_restart_fired_at IS NULL",
               (time.time(), event_id),
           )
       return (cur.rowcount or 0) > 0
   ```
2. In `hermes_api/server.py`, near line 632 (above `_deliver_event`), add `_maybe_trigger_auto_restart(event_id, lead, type_, payload_json)`:
   - Skip unless `type_ == "MILESTONE"`.
   - Skip unless `HERMES_AUTO_RESTART_WINDOW_UTC` is set AND `int(env) > int(time.time())`.
   - Parse payload `progress` field; require `RESTART REQUIRED` substring AND a `services:\s*([^)\n]+)` regex match.
   - Call `_store.claim_auto_restart(event_id)`; return early if False.
   - Spawn `subprocess.Popen(["setsid", "nohup", "sudo", "bash", "/opt/claude-soma/scripts/auto-restart-services.sh", services_str], stdin=DEVNULL, stdout=log_fh, stderr=STDOUT, start_new_session=True)`.
3. Wire `_maybe_trigger_auto_restart(event_id, lead, type_, payload_json)` inside `_handle_notify` BEFORE the deliver-thread spawn (so restart trigger is DM-throttle-independent).
4. In `notify_inject.sh`, add a jq filter to skip already-fired rows: `select((.auto_restart_fired_at // null) == null)` to the existing chain.

**Acceptance tests (extend):**
- LLM-ROUTINES: `test_list_routines_no_cloud_call` — mock `_call_claude_routines` to raise if called; call `list_routines`; assert no exception (i.e. cloud was not called) and the response merges registry+local+cron correctly.
- L-FINOTIFY unit `claim_auto_restart`: first call returns True + column set; second returns False + column unchanged.
- L-FINOTIFY unit `_maybe_trigger_auto_restart` table (6 branches): STARTED→no-spawn; MILESTONE no-RR→no-spawn; MILESTONE RR no-services→log-only-no-spawn; window expired→no-spawn; valid+claim-True→Popen called with expected argv; valid+claim-False→no-spawn.
- L-FINOTIFY integration: HTTP POST `/notify` with MILESTONE matching → assert 202 + `auto_restart_fired_at NOT NULL` in db + Popen called once.
- `notify_inject.sh` jq filter: extend `tests/scripts/test_notify_inject.py` with stubbed `/events` payload containing one fired + one unfired MILESTONE → assert setsid invoked once for the unfired.

**Live verification (post-deploy + restart):**
- LLM-ROUTINES: load `/admin/routines` in browser; `ps -ef | grep claude` shows NO new claude process spawned. Repeat 5 times. Routines list populates from sqlite.
- L-FINOTIFY: emit a test RESTART REQUIRED MILESTONE for `claude-soma-api.service`; tail `/tmp/auto-restart-services.log`; verify the listener-direct fire line appears within 1s; assert api MainPID changed.
- Operator next step (post-W1E): retire the overnight bash poller — `kill <PID>`; document in NEXT.md.

### W1F — I-MARKSERV: launch script + systemd unit realign

**Task ID:** PAF-W1F-MARKSERV
**Issue:** repo has `systemd/claude-soma-markserv.service` + `scripts/markserv-launch.sh` already, but configured for the BYPASSED `/staging:18080` design from PLAN-PARALLEL. The Caddyfile + live route use `/var/lib/claude-soma/relay:18081`. Unit is not installed; markserv is dead → `files.mayankgupta.in` returns 502.

**Files to modify (EXACT):**
- `scripts/markserv-launch.sh`
- `systemd/claude-soma-markserv.service`
- `tests/scripts/test_markserv_launch.py` (NEW)

**Fix:**
- `markserv-launch.sh`: default `STAGING_DIR=${SOMA_MARKSERV_ROOT:-/var/lib/claude-soma/relay}`; default `MARKSERV_PORT=${SOMA_MARKSERV_PORT:-18081}`; default `MARKSERV_ADDR=${SOMA_MARKSERV_ADDR:-127.0.0.1}`; add `--silent` to the markserv argv.
- `claude-soma-markserv.service`: `Description=Claude Soma file relay (markserv on /var/lib/claude-soma/relay served via files.mayankgupta.in)`. `WorkingDirectory=/var/lib/claude-soma/relay`. `Restart=always`, `RestartSec=5`. `After=network-online.target` + `Wants=network-online.target`. `User=ubuntu`, `Group=ubuntu`. `ExecStart=/opt/claude-soma/scripts/markserv-launch.sh`.
- Operator deploy step (lead documents in execution log; does not run from subagent): `sudo chown -R ubuntu:ubuntu /var/lib/claude-soma/relay && sudo cp /opt/claude-soma/systemd/claude-soma-markserv.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now claude-soma-markserv.service`. Verify via `curl -sI http://127.0.0.1:18081/` and (with basicauth) `curl -sIu "soma:$PASS" https://files.mayankgupta.in/`.

**Acceptance test:** `bash -n scripts/markserv-launch.sh` passes; `systemd-analyze verify systemd/claude-soma-markserv.service` passes (skip with explanation if not available in worktree); a dry-run that exports `SOMA_MARKSERV_ROOT=/tmp/test`, `SOMA_MARKSERV_PORT=18099`, runs the script for 2s with `&` then kills it, asserts the markserv process bound 127.0.0.1:18099.

### W1G — I-GATE-V3: orchestrator_gate.py + shim

**Task ID:** PAF-W1G-GATE-V3
**Issue:** S-GATE-V2 still has substring false-positives (`npm view pkg test`, `git log --grep="fetch this" --depth=5`, `docker logs run-name`) AND wrapper-bypass leakage (`bash -c "curl x"`, `eval "..."`, `nohup curl ...`, `$(curl ...)`) AND an infinite-loop edge in the env-strip loop. Bash parameter-expansion is too coarse for security parsing.

**Files to modify (EXACT):**
- NEW: `scripts/orchestrator_gate.py` (Python shlex parser)
- `scripts/orchestrator_gate.sh` (now a thin shim)
- `tests/scripts/test_orchestrator_gate.py` (extend with V3 test cases)

**Fix:**
- New `scripts/orchestrator_gate.py`: shebang `#!/usr/bin/env python3`. Read JSON from stdin. Tool-name denies (Edit/NotebookEdit/Write under sensitive paths/WebFetch/WebSearch/Skill/playwright MCPs/HF heavyweight MCPs) unchanged from V2.
- For `Bash` tool: `extract_first_cmd(cmd)` using `shlex.split` after `cmd.split("<<", 1)[0]` heredoc strip. Strip leading `VAR=val` env assignments via `ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")`. Strip leading wrappers (`sudo`, `exec`, `nohup`, `setsid`, `env`, `time`). For shell-c (`bash -c "..."`, `sh -c "..."`, `zsh -c "..."`, `dash -c "..."`) and `eval "..."` — RECURSE into the inner command. For `$(...)` substitution: fail-OPEN with `None` (log to activity.jsonl for later tightening).
- Once `(bin_name, tokens)` extracted, sub-command checks: `apt|apt-get install|update|upgrade`; `pip|pip3|pipx install`; `npm install|i|test`; `pnpm install|add|test`; `yarn add|install`; `cargo build|install|test`; `bun install`; `git clone|pull|push`; `git fetch` only when `--depth|--shallow-since` present; `docker build|run`; `make|cmake` (any); `pytest`; `claude` (preserve W1B+F3); `codex`; `ffmpeg -i`; `whisper-cli -f`; `curl|wget` only when target is NOT localhost/127.0.0.1/0.0.0.0.
- New `scripts/orchestrator_gate.sh`: 5-line shim that execs `python3 ${SCRIPT_DIR}/orchestrator_gate.py`. Respect `SOMA_ORCHESTRATOR_GATE_DISABLED=1` early-exit. Preserve hook contract (stdin→stdout JSON).

**Acceptance tests:** parametrize at minimum these inputs (positive cases ALLOW, negative cases DENY):
- ALLOW: `npm view some-pkg test`; `git log --grep="fetch this" --depth=5`; `docker logs run-name`; `FOO=bar` (empty after strip); `cd /tmp/curl-output && ls`; `curl http://127.0.0.1:9100/healthz`.
- DENY: `npm install foo`; `git clone https://x`; `docker build .`; `claude --print "hi"`; `bash -c "curl https://x"`; `eval "pip install foo"`; `nohup curl https://x`; `setsid wget https://y`.
- INFINITE-LOOP EDGE: `FOO=bar` (V2 bug); V3 must allow without hanging.

### W1H — L-CODEX: setsid timeout --kill-after wrapper

**Task ID:** PAF-W1H-CODEX
**Issue:** Codex wrapper is bash `timeout 120 codex exec ...` (GNU timeout = SIGTERM only). Codex traps SIGTERM and may exceed 120s in cleanup; setsid'd inferior helpers escape the killpg.

**Files to modify (EXACT):**
- `system_prompts/responsive_bot.md` (sections around lines 421-426 + 444-448)
- `skills/codex-image-gen/SKILL.md` (lines around 35-39)

**Fix:** replace every `timeout 120 codex exec ...` invocation pattern with `setsid timeout --kill-after=10 120 codex exec ...`. Widen the timeout-detection branch to accept exit code 137 (SIGKILL) alongside 124 (SIGTERM). Update the prose to say "hard-killed after 130s grace" instead of "2 min".

In `responsive_bot.md`, the dual-photo agent dispatch prompt (around line 421-426) must instruct subagents to use this wrapper. The SKILL frontmatter `allowed-tools` already includes `Bash(codex *)` — no change needed there.

**Adjacent flag (NOT in this PR):** `src/claude_soma/mcp_servers/grok_image/server.py:48-59` uses plain `subprocess.run(timeout=...)` without `start_new_session=True + killpg`. Same leak class. Tracked as follow-up.

**Acceptance test:** none required (markdown edits). Linter: `python3 -c "import yaml; yaml.safe_load(open('skills/codex-image-gen/SKILL.md').read().split('---')[1])"` passes.

**Live verification:** none directly verifiable until the next dual-photo invocation by a lead. Document in NEXT.md that the next dual-photo test should verify `pgrep -af codex` is empty within ~1s of wrapper return.

---

## Part 6 — Risk-tiered ordering

Within Wave 1 (all parallel): the FAN-OUT is simultaneous (one tool-use block with 8 Agent calls); the VERIFICATION order is "as each finishes." Critical items (LLM-ROUTINES — biggest token surface) are bundled in W1E which has the largest scope. If only ONE subagent is allowed to run, ship **W1E first** (closes the biggest leak AND lands listener-direct auto-restart for future waves).

Strict-priority order if user prefers serial (not recommended; parallel is faster + isolates conflicts):
1. **W1E** — closes biggest LLM cost surface + ships listener-direct path
2. **W1A** — fixes the Usage tab + ALSO eliminates daily `claude -p /usage` cost (complements T1 from prior round)
3. **W1D** — fixes Conversations + Memory (visible to operator)
4. **W1B** — fixes Broadcast (visible to operator)
5. **W1C** — fixes Projects DM (visible to operator)
6. **W1F** — restores files.mayankgupta.in (operator visibility of generated docs)
7. **W1G** — FI-GATE V3 (orchestrator UX, lower urgency)
8. **W1H** — codex wrapper (low frequency; runs only during dual-photo)

---

## Part 7 — Acceptance criteria per wave

### Wave 1 acceptance
- 8 commits on `origin/main`, each authored by `Mayank Gupta <techfreakworm@gmail.com>`.
- Pytest baseline preserved + new tests added per brief. No regressions.
- `/opt deploy: `git -C /opt/claude-soma pull --ff-only` succeeds.
- `claude-soma-channel.service` + `claude-soma-api.service` `ActiveEnterTimestamp` post the RESTART REQUIRED MILESTONE event timestamp.
- `claude-soma-frontend.service` rebuilt + restarted via `bash /opt/claude-soma/scripts/build_frontend.sh` (memory page change).
- `claude-soma-markserv.service` installed + enabled + `curl -sI http://127.0.0.1:18081/` returns 200.
- Browser smoke per tab: Usage shows non-zero values; Broadcast Telegram message arrives; Projects DM lands in lead tmux; Conversations lists threads; Memory shows text + 5 stats cards.
- LLM smoke: `for i in $(seq 1 5); do curl -s http://127.0.0.1:9000/api/routines >/dev/null; done; pgrep -af claude` — assert no transient claude processes spawned by the loop.

---

## Part 8 — Zero-LLM-Cost audit appendix (from S2)

**Audit completeness:** every endpoint mounted under `src/claude_soma/api/routes/` traced; repo-wide grep `grep -rn 'claude -p\|subprocess.*claude\|anthropic\|_call_claude\|_query_cloud' src/claude_soma/api/` returns hits ONLY in `routes/routines.py`.

| # | Endpoint | Method | File:Line | LLM? | Page-Load? | Disposition |
|---|---|---|---|---|---|---|
| 1 | `/admin/logs/{lead_name}` | GET | admin_logs.py:49 | No | Yes | clean |
| 2 | `/admin/broadcast` | POST | admin.py:25 | No (broken: no consumer) | No (button) | fix via W1B |
| 3 | `/admin/pause-all` | POST | admin.py:38 | No | No | clean |
| 4 | `/admin/upload/{lead_name}` | POST | admin_upload.py:84 | No | No | clean |
| 5 | `/conversations` | GET | conversations.py:12 | No (UDS RPC) | Yes | clean (data path broken: fix via W1D) |
| 6 | `/conversations/{thread_id}` | GET | conversations.py:18 | No | Yes | clean |
| 7 | `/events` | GET (SSE) | events.py:55 | No | Yes | clean |
| 8 | `/healthz` | GET | healthz.py:12 | No | Yes | clean |
| 9 | `/logs` | GET | logs.py:12 | No | Yes | clean |
| 10 | `/memory/{project}` | GET | memory.py:12 | No | Yes | clean (slug path broken: fix via W1D) |
| 11 | `/projects` | GET | projects.py:18 | No | Yes | clean |
| 12 | `/projects/{name}` | GET | projects.py:23 | No | No | clean |
| 13 | `/projects/{name}/team` | GET | projects.py:31 | No | Yes | clean |
| 14 | `/projects/{name}/message` | POST | projects.py:40 | No (stub: drops msg) | No | fix via W1C |
| 15 | `/projects/{name}/kill` | POST | projects.py:48 | No | No | clean |
| 16 | `/public/stats` | GET | public.py:15 | No | Yes | clean |
| 17 | **`/routines`** | **GET** | **routines.py:430** | **Yes (`claude -p`)** | **Yes** | **fix via W1E (Option A)** |
| 18 | `/routines/{id}/run` | POST | routines.py:448 | Yes | No (button click) | retained — user-initiated cost expected |
| 19 | `/usage` | GET | usage.py:32 | No (sqlite) | Yes | clean (writer broken: fix via W1A) |

**Off-the-table amplifier:** `_prewarm_routines_cache` thread spawn at `src/claude_soma/mcp_servers/hermes_api/server.py:945` — fixed via W1E delete.

**Cron / timer amplifier check:** `claude-soma-cache-refresh.timer` hits `/api/healthz` + `/api/public/stats` only — clean.

**Net effect post-W1E + W1A:** every admin page load served from sqlite + file reads + tmux/systemctl shell-outs. **Zero Claude credits consumed on any admin page load.** Matches the user's verbatim constraint.

---

## Part 9 — Already-shipped, notify-only

### I-FE-CP — `cp -r .next/static .next/standalone/.next/static`

**Status:** ALREADY SHIPPED. `scripts/build_frontend.sh:30-33` (commits `27e5f8b` + `7f7b729`) does:
```bash
mkdir -p .next/standalone/.next
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/static
cp -r public       .next/standalone/public
```
With post-build `ls` assertions. Confirmed live at `/opt/claude-soma/frontend/.next/standalone/.next/static/`.

**If the user is still seeing broken-chunks:** suspect the operator ran `pnpm build` directly instead of `bash scripts/build_frontend.sh`. Surface as a workflow note in NEXT.md; do not patch the script.

### I-GROK-BIN — grok binary path

**Status:** ALREADY SHIPPED. `src/claude_soma/mcp_servers/grok_image/server.py:19-32` `_resolve_grok_bin()` (commit `eb31cc9`) honors `GROK_BIN` env > `shutil.which("grok")` > `/usr/local/bin/grok` fallback. Sole call site is line 46 (confirmed by repo-wide grep). No hardcoded references elsewhere in `src/`, `scripts/`, `.mcp.json`, `system_prompts/`, or `wizard/`.

**Verification:** `GROK_BIN=/path/to/grok python3 -c "from claude_soma.mcp_servers.grok_image.server import _resolve_grok_bin; print(_resolve_grok_bin())"`.

If the user has a recent failing log with hardcoded path, request the log; absent that, this is a no-op.

---

## Part 10 — Relay-copy status for this plan

`files.mayankgupta.in` returns 502 because markserv died (no systemd unit yet). Will retry `sudo cp PLAN-ADMIN-FIXES.md /var/lib/claude-soma/relay/` AFTER W1F lands + the new `claude-soma-markserv.service` is enabled live. For now, the plan is available only in the repo at `/home/ubuntu/projects/soma-improver/claude-soma/PLAN-ADMIN-FIXES.md` and on `origin/main`.

---

## Part 11 — Open questions for user (before W1 dispatch)

1. Routines tab Option A (drop cloud) vs Option B (background timer)? Default A.
2. Broadcast in-handler vs separate drainer? Default A (in-handler).
3. Memory response include `path` field? Default Y.
4. Usage tier-vs-directory heuristic for interactive-vs-agent_sdk split? Default tier-based.
5. FI-GATE V3 `$(...)` fail-OPEN with telemetry, or fail-DENY? Default fail-OPEN.

Confirm any/all defaults or override before approving dispatch.

---

## Part 12 — Risk + rollback notes

- **W1E (LLM-ROUTINES) UX cost:** dropping cloud submission means cloud-row `last_run` / `next_run` render as `"—"` until something writes them. Operator-visible regression for the Routines tab. If unacceptable, ship Option B (background timer) as follow-up.
- **W1E (L-FINOTIFY) coordination risk:** if `notify_inject.sh` hook fires concurrently with the listener-direct path, the `claim_auto_restart` UPDATE serializes; only one wins. Existing flock + window-marker in `auto-restart-services.sh` catches any race that slips through. THREE layers of defense.
- **W1D frontend rebuild:** the memory page typed-response change requires `bash scripts/build_frontend.sh`. If the build fails, the dashboard regresses to the prior build. Mitigation: build_frontend.sh has its own assertion checks; rebuild reverts on failure.
- **W1F markserv `Restart=always`:** if markserv crashes in a loop, systemd will spin it indefinitely. Mitigate via `RestartSec=5` (5s minimum between restarts). The Caddyfile basicauth is unchanged, so a marketserv crash does NOT expose the relay dir publicly.
- **W1G FI-GATE V3 `$(...)` fail-OPEN:** the gate would temporarily allow command substitutions through. Activity-log telemetry surfaces any abuse; tighten to fail-DENY in a follow-up after 24-48h observation.

---

## Execution log (appended by lead during execution)

_(empty — to be filled after user approval + Wave 1 dispatch)_
