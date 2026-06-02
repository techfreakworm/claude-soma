# PLAN-ARCH-NOTIFY.md

**Status:** DRAFT — awaiting user approval. NO implementation.

**Generated:** 2026-06-02 by soma-improver. Methodology: 4 opus+max+sequential-thinking PLANNING subagents (S1 orchestrator-unaware design / S2 grok-image RCA / S3 cross-cutting + poller-survival / S4 test surface). Synthesized into a single coherent plan. Per-subagent reports archived at `/tmp/PAN-S{1,2,3,4}-*.md` on lead host.

**Scope:** two distinct items planned together because they share the FI-NOTIFY surface and one operator-visibility concern:
- **A — BUG-ORCHESTRATOR-UNAWARE-OF-LEAD-EVENTS** (architectural; queued in `BUGS_PLAN.md` post-W1E)
- **B — GROK-IMAGE MCP response parser drift** (today's live failure on prompt "a peacock sitting on a tree")

**Out of scope:** anything in `FAR-FETCHED.md`. No Bluesky scope.

---

## Headline summary

| Item | Status | Owner subagent | Restart impact |
|---|---|---|---|
| A1 Generalize W1E listener-direct dispatcher (Candidate E) | FIX REQUIRED | W1B | channel restart |
| A2 Schema migration: `action_fired_at REAL` + `action_key TEXT` columns | FIX REQUIRED | W1B (bundled) | channel restart (sqlite ALTER on EventStore init) |
| A3 `notify_inject.sh` jq filter update | FIX REQUIRED | W1B (bundled) | none (hook re-read) |
| A4 `auto-restart-services.sh` self-DM on completion (operator-visibility) | FIX REQUIRED | W1B (bundled) | none |
| A5 Listener self-healthcheck systemd timer (Candidate F) | FIX REQUIRED | W1C | new systemd timer (daemon-reload + enable) |
| A6 Retire overnight bash poller (operator step) | OPERATOR ACTION | — | n/a |
| A7 Backfill soma-improver transient unit env (HERMES_LEAD_NAME + endpoint) | DEFERRED | — | not blocking; queued |
| B1 grok parser refactor (tiered extractor + 8 shapes) | FIX REQUIRED | W1A | channel restart |
| B2 grok parser tests (8 cases) | FIX REQUIRED | W1A (bundled) | none |
| B3 `sessionId` vs `session_id` envelope-key fix | FIX REQUIRED | W1A (bundled) | channel restart |
| C Orch-wake end-to-end + W1E coordination tests | FIX REQUIRED | W1B (bundled with A) | none |

**Tally:** 9 code-fix candidates across 3 Wave-1 subagents + 1 operator step + 1 deferred follow-up.

---

## Part 1 — The reframing

**Critical insight from S1.** The user's hard constraint ("NO LLM tokens for analytical/operational processes") **collapses the design space**. Events split into two strict classes:

- **Scriptable / operational** (restart, deploy, cleanup, file-push): must be handled in a side channel; zero LLM tokens.
- **Requires LLM judgment** (NEEDS_INPUT, ERROR): the user is the trigger. They get the Telegram DM (already works). When they reply, `notify_inject.sh` wakes the orchestrator naturally with full context. No unilateral wake needed.

So the actual gap is NOT "wake the orchestrator for NEEDS_INPUT" — that's already correct by design. The gap is "generalize W1E's listener-direct script-dispatch pattern beyond RESTART REQUIRED to all scriptable event types, and add self-healthcheck so the surfacing path itself is observable."

This reframing rules out four superficially-attractive candidates from S1's analysis:
- **Candidate A** (FS-watcher + SessionStart hook + ScheduleWakeup) — broken: SessionStart does not fire mid-`--channels` session; sentinels rot.
- **Candidate B** (synthetic UserPromptSubmit via tmux send-keys) — violates zero-token constraint (~30-100k tokens/event).
- **Candidate C** (bash polling daemon + Bot API direct) — duplicates existing `_send_proactive_dm` path; doesn't solve scriptable-action gap.
- **Candidate D** (hybrid notify_inject + bash poller) — relabeling of C.

The winning shape is the natural generalization of W1E.

---

## Part 2 — Architectural winner (S1 Candidate E + F)

### A. Generalized listener-direct script dispatcher (Candidate E)

Refactor `_maybe_trigger_auto_restart` → `_maybe_trigger_automation` with a Python dispatch table:

```python
_AUTOMATION_DISPATCH: list[tuple[str, callable, str, str]] = [
    # (event_type, predicate(payload)->bool, handler_key, handler_script)
    ("MILESTONE",
     lambda p: _RESTART_REQUIRED_RE.search(p.get("progress", "") or ""),
     "restart",
     "/opt/claude-soma/scripts/automation-handlers/restart.sh"),
    # Future entries (deploy, cleanup, file-push) trivially added here.
]
```

Each entry maps `(event_type, payload_predicate)` → `(handler_key, handler_script)`. The handler runs as `setsid + nohup + sudo bash + start_new_session=True` — proven escape pattern from W1E (which S3 empirically verifies survives the parent cgroup tear-down because `setsid` detaches the process tree before the SIGTERM arrives via systemd's `KillMode=control-group`).

Critically, S3 also empirically confirms that a **persistent** bash daemon launched the same way does NOT survive — its parent cgroup membership is inherited, so a `systemctl restart claude-soma-channel.service` reaps it. The dispatcher's `Popen` works because the handler script itself is short-lived; the channel-side launching is the seed but the handler outlives the seed via `start_new_session=True`.

### B. Atomic action claim with key (schema change)

Drop `auto_restart_fired_at` (which shipped one push ago in W1E) and replace with the more general:

```sql
ALTER TABLE lead_events ADD COLUMN action_fired_at REAL;
ALTER TABLE lead_events ADD COLUMN action_key TEXT;
CREATE INDEX IF NOT EXISTS idx_le_unfired
    ON lead_events (action_fired_at)
    WHERE action_fired_at IS NULL;
```

Rationale for drop-and-replace rather than add-alongside: `auto_restart_fired_at` is <1 day live (W1E `cb40e75` 2026-06-02). No operator history depends on it. Carrying both columns forever creates dual-claim ambiguity. The new schema records WHICH handler fired (`action_key`), enabling per-handler idempotency.

New method on `EventStore`:

```python
def claim_action(self, event_id: int, key: str) -> bool:
    """Atomic single-fire claim per (event_id, key) pair."""
    with self._lock:
        cur = self._conn.execute(
            "UPDATE lead_events SET action_fired_at = ?, action_key = ? "
            "WHERE id = ? AND action_fired_at IS NULL",
            (time.time(), key, event_id),
        )
    return (cur.rowcount or 0) > 0
```

This generalizes W1E's `claim_auto_restart`. Migration path: rename the W1E method internally to `claim_action`; pass `key="restart"` from the dispatcher's first entry.

### C. Operator-visibility: self-DM at handler completion

Append to `auto-restart-services.sh` (and any future handler):

```bash
# After the restart loop finishes successfully:
source ~/.claude/channels/telegram/.env
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl -sX POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
    --data-urlencode "chat_id=${HERMES_NOTIFY_CHAT_ID:-935376085}" \
    --data-urlencode "text=automation 'restart' completed at $ts: ${RESTART_SERVICES}" \
    >/dev/null
```

Without this, automation execution is invisible to the operator. With it, every automated action produces a Telegram trail: (a) the original event DM, (b) the automation-confirm DM. Zero Claude tokens.

### D. Listener self-healthcheck timer (Candidate F)

A separate concern from A-C: if the listener subprocess silently dies (OOM, segfault, hung mutex), the entire dispatcher pipeline is dark and the operator has no signal. Ship a new oneshot systemd timer:

```ini
# systemd/claude-soma-listener-healthcheck.timer
[Unit]
Description=Run listener healthcheck every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Unit=claude-soma-listener-healthcheck.service
[Install]
WantedBy=timers.target

# systemd/claude-soma-listener-healthcheck.service
[Unit]
Description=Claude Soma listener healthcheck
[Service]
Type=oneshot
User=ubuntu
ExecStart=/opt/claude-soma/scripts/listener-healthcheck.sh
```

Bash script self-rate-limits via a state file `/var/lib/claude-soma/listener-healthcheck.state` so the operator gets ONE DM per outage, not one every 5 minutes. State file is cleared on recovery (next tick when `/health` returns OK).

### E. notify_inject.sh jq filter update

Change the existing W1E-era filter from:

```sh
select((.auto_restart_fired_at // null) == null)
```

to:

```sh
select((.action_fired_at // null) == null)
```

Same idempotency contract; new column name. The hook still fires for RESTART REQUIRED events if (and only if) the listener-direct path failed to claim them first.

---

## Part 3 — Item B: grok-image parser drift

### B1. Root cause (from S2 with live capture)

S2 ran grok 0.2.11 manually three times and captured a non-deterministic format:

**Shape A (current parser handles):** `![Peacock on a tree](/home/ubuntu/.grok/sessions/.../images/1.jpg)` (Markdown image link, parenthesized).

**Shape B (current parser FAILS):** `**Image generated:** \`/home/ubuntu/.grok/sessions/.../images/1.jpg\`` (bold-prefix, backtick-quoted path).

Current regex `_IMAGE_LINK_RE = re.compile(r"\(([^)]+\.(?:jpg|png))\)")` requires the literal `(...)` form. Backticks miss.

Secondary defects:
- Envelope key drift: live grok returns `sessionId` (camelCase). The code reads `envelope.get("session_id")` (snake) and silently falls through to `uuid.uuid4()`. Cosmetic but breaks trace correlation.
- `_IMAGE_LINK_RE` requires `.jpg`/`.png` literal suffix. If grok ever emits `.jpeg` or `.webp`, parser dies.
- `src.exists()` works correctly because the on-disk directory name actually contains `%2F` (the path component is `%2Fhome%2Fubuntu...`). DO NOT URL-decode in the parser.

### B2. Tiered extractor (S2 design)

Replace the single regex with an ordered list:

```python
_PATTERNS = [
    # 1. Markdown image:  ![alt](/abs/path.jpg)
    re.compile(r"!\[[^\]]*\]\((?P<p>[^)\s]+\.(?:jpg|jpeg|png|webp))\)"),
    # 2. Markdown link (no bang):  [text](/abs/path.jpg)
    re.compile(r"(?<!\!)\[[^\]]*\]\((?P<p>[^)\s]+\.(?:jpg|jpeg|png|webp))\)"),
    # 3. Backtick-quoted path:  `/abs/path.jpg`
    re.compile(r"`(?P<p>/[^`\s]+\.(?:jpg|jpeg|png|webp))`"),
    # 4. Bare absolute path with image extension anywhere in text
    re.compile(r"(?<![`\(\[])(?P<p>/[^\s`)\]<>]+\.(?:jpg|jpeg|png|webp))(?![`\)\]])"),
    # 5. Plain http(s) URL
    re.compile(r"(?P<p>https?://[^\s)`\]<>]+\.(?:jpg|jpeg|png|webp))"),
]

def _extract_image_target(text: str) -> str | None:
    for pat in _PATTERNS:
        m = pat.search(text)
        if m:
            return m.group("p")
    return None
```

Plus:
- Accept envelope key in either case: `envelope.get("sessionId") or envelope.get("session_id") or str(uuid.uuid4())`.
- Multi-image: return the FIRST match (dual-photo pipeline runs grok+codex in parallel; multi-image-per-call is unusual; first is canonically "primary").
- Refusal contract: if no pattern matches → `RuntimeError("grok returned no image reference: <first 300 chars of text>")`. Operator sees the actual refusal/rate-limit text in the error.
- URL handling: if the parsed target starts with `http://`/`https://`, `urllib.request.urlretrieve` it into `output_dir` and return the local path. Existing path-handling preserved otherwise.

### B3. Keep grok-image as a provider

Per S2's keep-vs-remove analysis: dual-photo dispatch in `responsive_bot.md:391-428` deliberately runs grok+codex in parallel and lets the user pick. Removing grok cuts that A/B in half. Cost of fixing (~30 LOC parser + ~120 LOC tests) is one-time; cost of removing is cascading edits + permanent loss of redundancy. **Keep.**

---

## Part 4 — Dependency DAG + Wave structure

```
                     ┌────────────────────────────────────────────────────┐
                     │ WAVE 1 — 3 parallel sonnet+max+seq-thinking impl   │
                     ├────────────────────────────────────────────────────┤
                     │ W1A: GROK-PARSER refactor (tiered + envelope-key)  │
                     │ W1B: ORCH-WAKE generalization (dispatcher + schema │
                     │      + jq filter + handler self-DM + 8 new tests)  │
                     │ W1C: SELF-HEALTHCHECK timer (bash + 2 .units)      │
                     └──────────────┬─────────────────────────────────────┘
                                    │
                          channel restart + new timer enable
                                    │
                                    ▼
                            Live verification
                                    │
                                    ▼
                              COMPLETED report
```

Single wave; 3 file-disjoint subagents. No Wave 2 needed for this push.

### File-touch matrix (no conflicts)

| File | Touched by |
|---|---|
| `src/claude_soma/mcp_servers/grok_image/server.py` | W1A only |
| `tests/test_dual_image_dispatch.py` (or new test file) | W1A only |
| `src/claude_soma/mcp_servers/hermes_api/server.py` | W1B only |
| `src/claude_soma/mcp_servers/hermes_api/notify_store.py` | W1B only |
| `scripts/notify_inject.sh` | W1B only |
| `scripts/auto-restart-services.sh` | W1B only |
| NEW `scripts/automation-handlers/restart.sh` (symlink) | W1B only |
| `tests/mcp_servers/test_hermes_notify.py` | W1B only |
| `tests/mcp_servers/test_hermes_api_listener.py` | W1B only |
| NEW `scripts/listener-healthcheck.sh` | W1C only |
| NEW `systemd/claude-soma-listener-healthcheck.service` | W1C only |
| NEW `systemd/claude-soma-listener-healthcheck.timer` | W1C only |
| NEW `tests/scripts/test_listener_healthcheck.py` | W1C only |

W1A and W1B both load via channel claude session (stdio MCPs); they require the same channel restart at wave end but don't conflict at code level.

---

## Part 5 — Restart matrix

| Service / artifact | Touched by | Action |
|---|---|---|
| `claude-soma-channel.service` (parent of hermes_api + grok_image stdio MCPs) | W1A, W1B | restart |
| `claude-soma-api.service` (uvicorn dashboard backend) | none | unaffected |
| `claude-soma-frontend.service` | none | unaffected |
| `claude-soma-listener-healthcheck.timer` (NEW) | W1C | `cp` to `/etc/systemd/system/` + `daemon-reload` + `enable --now` |

Deploy procedure (per memory notes): `git push origin main` → on VPS `git -C /opt/claude-soma pull --ff-only` → `sudo cp /opt/claude-soma/systemd/claude-soma-listener-healthcheck.{service,timer} /etc/systemd/system/` → `sudo systemctl daemon-reload` → `sudo systemctl enable --now claude-soma-listener-healthcheck.timer` → emit RESTART REQUIRED MILESTONE with `services: claude-soma-channel.service`.

**Auto-restart compatibility:** the W1B refactor RENAMES `claim_auto_restart` → `claim_action`. The new dispatcher's RESTART REQUIRED entry is the only consumer of the old call site. Verify the rename is total before pushing. Channel restart picks up new code immediately.

---

## Part 6 — Coordination protocol

**Git hygiene (binding):** every subagent stages SPECIFIC paths only — no `git add -A`, no `.`. Push to `origin/main` directly. On non-fast-forward: rebase, no force. On rebase conflict: STOP-AND-SURFACE to `/tmp/<TASK-ID>-STOP.md` and exit non-zero.

**Author convention:** `Mayank Gupta <techfreakworm@gmail.com>` sole author. No `Co-Authored-By`, no Claude footer, no emoji.

**Worktree isolation:** all 3 Wave-1 subagents run with `isolation: worktree` (matching the established W4/W5 pattern).

**Test discipline:** each subagent runs its own pytest before pushing. Must not regress the prior 801-pass baseline.

---

## Part 7 — Pre-decided defaults (3 open questions)

These had multiple sound options; defaults chosen for the planning recommendation:

1. **W1B schema migration approach:** drop-and-replace `auto_restart_fired_at` → `action_fired_at` + `action_key`. Alternative was additive (keep both). Rationale: W1E column is <1 day old; no operator dependency yet; cleaner long-term shape. Risk mitigation: defensive ALTER-TABLE migration with try/except (same pattern W1E used) so a partial deploy doesn't corrupt the DB.
2. **W1A multi-image policy:** return the FIRST extracted image when grok produces multiple. Alternative: return all + let caller choose. Rationale: dual-photo pipeline already handles A/B at a higher level; single-call multi-image is unusual.
3. **W1C self-healthcheck DM rate-limit:** ONE alert per outage (cleared on recovery). Alternative: every 5 min until acknowledged. Rationale: avoid alert fatigue; the existing event-DM path covers actively-firing incidents.

User can override any default before approving dispatch.

---

## Part 8 — Per-candidate subagent briefs (paste-ready)

All briefs share this preamble:

> You are a sonnet+max+sequential-thinking implementation subagent. Use `mcp__sequential-thinking__sequentialthinking` with `--effort max` BEFORE writing code. Work in your fresh git worktree (off `origin/main`). Read the listed files BEFORE writing. Stage SPECIFIC paths only — never `git add -A`. Commit author `Mayank Gupta <techfreakworm@gmail.com>`. No Co-Authored-By, no Claude footer, no emoji. Push as `git push origin HEAD:main`. On non-fast-forward: rebase, re-push. On conflict: STOP-AND-SURFACE to `/tmp/<TASK-ID>-STOP.md` and exit non-zero. Report commit SHA + diffstat + pytest result.

### W1A — GROK-PARSER refactor

**Task ID:** PAN-W1A-GROK-PARSER

**Leak:** `src/claude_soma/mcp_servers/grok_image/server.py:16` `_IMAGE_LINK_RE` only handles Markdown link shape `(...)`. grok 0.2.11 non-deterministically returns backtick-quoted shape `` ` /path.jpg ` ``, causing `RuntimeError("no image link found in grok text field")`. Plus envelope key drift: code reads `session_id` (snake) but live grok returns `sessionId` (camelCase) → silent UUID fallback.

**Files to modify (EXACT):**
- `src/claude_soma/mcp_servers/grok_image/server.py`
- `tests/test_dual_image_dispatch.py` (extend) OR `tests/mcp_servers/test_grok_image.py` (NEW)

**Fix:**
1. Replace `_IMAGE_LINK_RE` with `_PATTERNS` ordered list of 5 regexes (Markdown image / Markdown link / backtick path / bare path / plain URL — all accepting `.jpg|.jpeg|.png|.webp`).
2. New helper `_extract_image_target(text: str) -> str | None` iterating `_PATTERNS` in order; returns first match's `p` group.
3. In `generate_image_impl`:
   - On no-match: raise `RuntimeError("grok returned no image reference: " + text[:300])` — include offending text snippet so operator can diagnose refusals.
   - If matched target starts with `http://`/`https://`: `urllib.request.urlretrieve` into `output_dir` and use that local path.
   - Envelope key fix: `session_id = envelope.get("sessionId") or envelope.get("session_id") or str(uuid.uuid4())`.

**Acceptance tests (8 cases from S2/S4 design):**
- B1 markdown_image_link: text `"![alt](/tmp/x.jpg)"` → success, path suffix `.jpg`.
- B2 plain_png_url: text `"https://x.test/a.png"` → success (urlretrieve mocked).
- B3 local_path_no_url: text `"/tmp/result.png"` → success.
- B4 multi_paragraph_text_extracts_url: text with markdown link buried in prose → success.
- B5 refusal_text_raises_with_helpful_message: text `"I cannot generate that image..."` → RuntimeError; message contains refusal snippet.
- B6 empty_stdout_raises_distinct_error: stdout empty → RuntimeError with disambiguating message.
- B7 json_text_field_with_url_only: envelope `text` is plain URL (no markdown) → success.
- B8 camelcase_session_id: envelope has `sessionId` only → returned `session_id` matches (not random UUID).

Use `monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout=<JSON envelope>, stderr=""))` per test. Pre-create fixture files in `tmp_path` via `Path.write_bytes(b"\xff\xd8\xff")`.

**Standing constraints:**
- Read `src/claude_soma/mcp_servers/grok_image/server.py` + `tests/test_dual_image_dispatch.py` FIRST.
- Use mcp__sequential-thinking__sequentialthinking liberally.
- Stage SPECIFIC: `git add src/claude_soma/mcp_servers/grok_image/server.py tests/test_dual_image_dispatch.py` (or the new test file).
- `pytest tests/test_dual_image_dispatch.py -v` (or the new file) must pass.
- Push `git push origin HEAD:main`. Rebase on rejection. STOP-AND-SURFACE to `/tmp/PAN-W1A-GROK-PARSER-STOP.md` on conflict.

**Report:** commit SHA + diffstat + pytest summary + 8/8 test ids confirmed.

### W1B — ORCH-WAKE generalization

**Task ID:** PAN-W1B-ORCH-WAKE (LARGEST WAVE-1 SUBAGENT — pace yourself)

**Leak:** W1E shipped a single specific listener-direct path for RESTART REQUIRED MILESTONE (`_maybe_trigger_auto_restart` + `claim_auto_restart` + `auto_restart_fired_at` column). All OTHER scriptable actions (deploy, file-push, cleanup) need the same plumbing. The current single-purpose schema can't disambiguate multiple handler types.

**Files to modify (EXACT):**
- `src/claude_soma/mcp_servers/hermes_api/server.py` (refactor `_maybe_trigger_auto_restart` → `_maybe_trigger_automation` + dispatch table)
- `src/claude_soma/mcp_servers/hermes_api/notify_store.py` (schema migration + rename method)
- `scripts/notify_inject.sh` (jq filter rename)
- `scripts/auto-restart-services.sh` (append self-DM)
- NEW: `scripts/automation-handlers/restart.sh` (symlink to `../auto-restart-services.sh` — git can track symlinks)
- `tests/mcp_servers/test_hermes_notify.py` (extend with cluster A + C)
- `tests/mcp_servers/test_hermes_api_listener.py` (extend)
- `tests/scripts/test_notify_inject.py` (extend)

**Fix:**

1. **Schema migration in `notify_store.py`:**
   - In `_SCHEMA` `CREATE TABLE lead_events`, replace `auto_restart_fired_at REAL` with `action_fired_at REAL` and add `action_key TEXT`. (Fresh DBs get the new schema.)
   - In `EventStore.__init__` after `executescript(_SCHEMA)`: defensive migration —
     ```python
     try: self._conn.execute("ALTER TABLE lead_events ADD COLUMN action_fired_at REAL")
     except sqlite3.OperationalError: pass
     try: self._conn.execute("ALTER TABLE lead_events ADD COLUMN action_key TEXT")
     except sqlite3.OperationalError: pass
     # Backfill from W1E column if it exists (best-effort):
     try: self._conn.execute("UPDATE lead_events SET action_fired_at = auto_restart_fired_at, action_key = 'restart' WHERE auto_restart_fired_at IS NOT NULL AND action_fired_at IS NULL")
     except sqlite3.OperationalError: pass
     ```
   - Add index `CREATE INDEX IF NOT EXISTS idx_le_unfired ON lead_events (action_fired_at) WHERE action_fired_at IS NULL` (use the same defensive try/except for IF NOT EXISTS support).
   - Rename `claim_auto_restart(event_id) -> bool` → `claim_action(event_id, key) -> bool` (UPDATE WHERE id=? AND action_fired_at IS NULL; SET action_fired_at=now, action_key=key).
   - Delete `auto_restart_fired_at` column reference from `_SCHEMA` going forward. The column may remain on existing DBs (sqlite ALTER DROP requires v3.35+; safer to leave dangling).

2. **`hermes_api/server.py` refactor:**
   - Replace `_maybe_trigger_auto_restart(event_id, lead, type_, payload_json)` with `_maybe_trigger_automation(event_id, lead, type_, payload_json)`.
   - Define `_AUTOMATION_DISPATCH: list[tuple[str, Callable[[dict], bool], str, str]]` module-level:
     ```python
     _AUTOMATION_DISPATCH = [
         ("MILESTONE",
          lambda p: bool(_RESTART_REQUIRED_RE.search(p.get("progress", "") or "")),
          "restart",
          "/opt/claude-soma/scripts/automation-handlers/restart.sh"),
     ]
     ```
   - `_maybe_trigger_automation` loops the table: window-check first; payload-parse; per-entry predicate; on hit, `claim_action(event_id, key)`; if claim won, extract handler args (services list for "restart" via `_SERVICES_RE`), spawn `subprocess.Popen(["setsid", "nohup", "sudo", "bash", script, *args], stdin=DEVNULL, stdout=open(f"/var/log/claude-soma/automation-{key}.log", "a"), stderr=STDOUT, start_new_session=True)`. First-match-wins; break after firing one handler.
   - Keep the `_RESTART_REQUIRED_RE` and `_SERVICES_RE` constants. Add `_AUTO_RESTART_LOG_PATH` → `_AUTOMATION_LOG_DIR = "/var/log/claude-soma"`.
   - Wire call site in `_handle_notify` unchanged: same line position, just renamed function.

3. **`notify_inject.sh` jq filter rename:**
   - Find the existing `select((.auto_restart_fired_at // null) == null)` line in the RESTART_SERVICES jq chain.
   - Replace with `select((.action_fired_at // null) == null)`.

4. **`auto-restart-services.sh` self-DM:**
   - At the end of the script's restart loop (after the per-service `systemctl restart` iterations succeed), append:
     ```bash
     # Self-DM operator with automation completion (zero LLM tokens)
     if [[ -r ~/.claude/channels/telegram/.env ]]; then
         source ~/.claude/channels/telegram/.env
         ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
         curl -sX POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
             --data-urlencode "chat_id=${HERMES_NOTIFY_CHAT_ID:-${TELEGRAM_CHAT_ID:-935376085}}" \
             --data-urlencode "text=automation 'restart' completed at $ts: $1" \
             --max-time 5 >/dev/null 2>&1 || true
     fi
     ```
   - Wrap in `|| true` so a Telegram failure does not affect the script's exit code (the restart already happened).

5. **NEW `scripts/automation-handlers/restart.sh`:**
   - `mkdir -p scripts/automation-handlers/`
   - `ln -sf ../auto-restart-services.sh scripts/automation-handlers/restart.sh` (relative symlink). Verify git tracks symlinks via `git ls-files -s` (mode 120000).

**Acceptance tests (extend per S4 design):**

Cluster A (in `tests/mcp_servers/test_hermes_notify.py`):
- A1 `test_claim_action_first_wins_per_key`: first `claim_action(eid, "restart")` returns True + column set; second returns False; `claim_action(eid, "deploy")` on same event returns False too (idempotent across keys at the row level, single fire per event).
- A2 `test_maybe_trigger_skips_non_milestone`: STARTED payload → no Popen.
- A3 `test_maybe_trigger_skips_no_restart_required`: MILESTONE without "RESTART REQUIRED" → no Popen.
- A4 `test_maybe_trigger_window_expired`: env unset → no Popen.
- A5 `test_maybe_trigger_fires_when_valid`: env in future + valid payload + claim wins → Popen called with `setsid + nohup + sudo + bash + restart.sh + services`. `start_new_session=True`.
- A6 `test_maybe_trigger_no_fire_when_claim_lost`: pre-claim event → no Popen on subsequent call.

Cluster C (W1E coordination):
- C1 `test_restart_milestone_via_handle_notify_fires_dispatch`: POST `/notify` with MILESTONE matching `progress="RESTART REQUIRED ... (services: claude-soma-channel.service)"` and env in future. Assert 202 + DB row's `action_fired_at NOT NULL` + `action_key = "restart"` + Popen called once.
- C2 `test_duplicate_restart_milestones_each_fire_only_once`: insert two identical MILESTONE rows; drive `_handle_notify` for each. Assert `claim_action` returned True only for the first (rowcount evidence); Popen called exactly once.

`notify_inject.sh` jq filter test (in `tests/scripts/test_notify_inject.py`):
- Extend the existing jq-filter test with a stubbed `/events` payload containing one fired and one unfired MILESTONE; assert `setsid` invoked at most once and only for the unfired row (now keyed on `action_fired_at`).

**Standing constraints:**
- READ all listed files first. Use mcp__sequential-thinking__sequentialthinking liberally.
- Stage SPECIFIC: `git add src/claude_soma/mcp_servers/hermes_api/server.py src/claude_soma/mcp_servers/hermes_api/notify_store.py scripts/notify_inject.sh scripts/auto-restart-services.sh scripts/automation-handlers/restart.sh tests/mcp_servers/test_hermes_notify.py tests/mcp_servers/test_hermes_api_listener.py tests/scripts/test_notify_inject.py`.
- Run `pytest tests/mcp_servers/test_hermes_notify.py tests/mcp_servers/test_hermes_api_listener.py tests/scripts/test_notify_inject.py -v`. Must pass.
- Push `git push origin HEAD:main`. Rebase on rejection. STOP-AND-SURFACE to `/tmp/PAN-W1B-ORCH-WAKE-STOP.md` on conflict.

**Report:** commit SHA + diffstat + pytest summary + confirmation that `auto_restart_fired_at` references were renamed totally + the symlink mode (120000) is preserved.

### W1C — SELF-HEALTHCHECK timer

**Task ID:** PAN-W1C-HEALTHCHECK

**Leak:** if the FI-NOTIFY listener subprocess silently dies, the entire surfacing pipeline is dark and the operator has no signal. No existing healthcheck monitors the listener.

**Files to modify (EXACT):**
- NEW: `scripts/listener-healthcheck.sh`
- NEW: `systemd/claude-soma-listener-healthcheck.service`
- NEW: `systemd/claude-soma-listener-healthcheck.timer`
- NEW: `tests/scripts/test_listener_healthcheck.py`

**Fix:**

`scripts/listener-healthcheck.sh`:
```bash
#!/usr/bin/env bash
set -uo pipefail
STATE=/var/lib/claude-soma/listener-healthcheck.state
mkdir -p "$(dirname "$STATE")"
if curl -sf --max-time 3 http://127.0.0.1:9100/health 2>/dev/null | grep -q '"status":"ok"'; then
    [[ -f "$STATE" ]] && rm -f "$STATE"
    exit 0
fi
[[ -f "$STATE" ]] && exit 0  # already alerted
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "$TS" > "$STATE"
if [[ -r ~/.claude/channels/telegram/.env ]]; then
    source ~/.claude/channels/telegram/.env
    curl -sX POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        --data-urlencode "chat_id=${HERMES_NOTIFY_CHAT_ID:-${TELEGRAM_CHAT_ID:-935376085}}" \
        --data-urlencode "text=ALERT: hermes_api listener /health failed at $TS" \
        --max-time 5 >/dev/null 2>&1 || true
fi
```

`systemd/claude-soma-listener-healthcheck.service`:
```ini
[Unit]
Description=Claude Soma listener healthcheck
[Service]
Type=oneshot
User=ubuntu
EnvironmentFile=/etc/claude-soma/secrets.env
ExecStart=/opt/claude-soma/scripts/listener-healthcheck.sh
```

`systemd/claude-soma-listener-healthcheck.timer`:
```ini
[Unit]
Description=Run listener healthcheck every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Unit=claude-soma-listener-healthcheck.service
[Install]
WantedBy=timers.target
```

`tests/scripts/test_listener_healthcheck.py`:
- `test_bash_syntax`: `bash -n scripts/listener-healthcheck.sh` returns 0.
- `test_state_file_path_present`: source contains `/var/lib/claude-soma/listener-healthcheck.state`.
- `test_telegram_curl_present`: source contains `api.telegram.org/bot` AND `sendMessage`.
- `test_systemd_analyze`: `systemd-analyze verify systemd/claude-soma-listener-healthcheck.{service,timer}` returns 0 (skip if unavailable).
- `test_rate_limit_via_state_file`: simulate two consecutive runs with the state file pre-populated; mock curl `/health` to fail; assert only the FIRST run sends a Telegram DM. (Use bash mock-by-PATH approach.)

**Standing constraints:**
- Stage SPECIFIC: `git add scripts/listener-healthcheck.sh systemd/claude-soma-listener-healthcheck.service systemd/claude-soma-listener-healthcheck.timer tests/scripts/test_listener_healthcheck.py`.
- `chmod +x scripts/listener-healthcheck.sh` before `git add`; verify with `git update-index --chmod=+x` if needed.
- `pytest tests/scripts/test_listener_healthcheck.py -v` must pass.
- Push `git push origin HEAD:main`. Rebase on rejection. STOP-AND-SURFACE to `/tmp/PAN-W1C-HEALTHCHECK-STOP.md` on conflict.

**Report:** commit SHA + diffstat + pytest summary + operator deploy steps (lead will run): `sudo cp /opt/claude-soma/systemd/claude-soma-listener-healthcheck.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now claude-soma-listener-healthcheck.timer`.

---

## Part 9 — Zero-LLM-cost audit

| Path | Claude tokens consumed? |
|---|---|
| Listener receives POST `/notify` → inserts row | 0 |
| `_maybe_trigger_automation` evaluates dispatch table | 0 |
| `claim_action` atomic UPDATE | 0 |
| Popen `setsid + nohup + sudo + bash + restart.sh` | 0 |
| Handler script executes `systemctl restart` + curl Bot API self-DM | 0 |
| `listener-healthcheck.sh` polls `/health` + curl Bot API on outage | 0 |
| Grok parser refactor (W1A) | 0 (pure regex) |

**Net effect:** every component in this push is deterministic / shell / SQL / HTTP. No new Claude tokens consumed during normal operation. Matches the user's hard constraint.

---

## Part 10 — Acceptance criteria

### Wave 1 acceptance
- 3 commits on `origin/main`, each authored by `Mayank Gupta <techfreakworm@gmail.com>`.
- Pytest baseline preserved + new tests added (~18 new cases). No regressions.
- `/opt deploy`: `git -C /opt/claude-soma pull --ff-only` succeeds.
- `sudo cp systemd/claude-soma-listener-healthcheck.{service,timer} /etc/systemd/system/` + `daemon-reload` + `enable --now`.
- `claude-soma-channel.service` `ActiveEnterTimestamp` post the RESTART REQUIRED MILESTONE.
- Live W1A check: invoke `mcp__grok-image__generate_image(prompt="a peacock sitting on a tree")` against the live MCP — should return a valid image path regardless of which output shape grok emits.
- Live W1B check: insert a synthetic MILESTONE with `progress="RESTART REQUIRED test_w1b_dispatch (services: claude-soma-api.service)"` while window is open; assert (a) `action_fired_at` set in sqlite, (b) `action_key='restart'`, (c) api restart visible in `systemctl status`, (d) Telegram DM "automation 'restart' completed..." arrives within 10s.
- Live W1C check: SIGKILL the hermes_api MCP subprocess; wait 5-10 min; assert ONE Telegram DM with "ALERT: hermes_api listener /health failed at..." arrives; then `systemctl restart claude-soma-channel.service`; verify the state file clears and no further alerts fire.

---

## Part 11 — Risk + rollback

- **W1B schema migration risk:** drop-and-replace of `auto_restart_fired_at` is one-way at the column level (sqlite ALTER DROP needs v3.35+). Mitigation: defensive try/except ALTER + best-effort BACKFILL from old column. If live DB rejects, the new code falls back to no-op (claim_action will silently fail to claim — equivalent to "first call always returns True for the row since new column is NULL"). Operator can manually verify schema after deploy with `sqlite3 /opt/claude-soma/registry.sqlite ".schema lead_events"`.
- **W1B rename total-coverage risk:** if any caller of `claim_auto_restart` survives the rename, ImportError on channel restart. Mitigation: grep the entire repo for `claim_auto_restart` before push and confirm zero hits other than the renamed method.
- **W1A regex over-match risk:** the new tiered extractor is greedy. Mitigation: pattern #4 (bare absolute path) is the most permissive; the negative lookaround `(?<![`\(\[])` keeps it from matching paths already inside parens/brackets/backticks. The 8 test cases pin the expected behavior; future drift requires adding a case before the patterns are touched.
- **W1C alert fatigue:** the rate-limit state file prevents repeat alerts within an outage. Risk: if the state file is deleted (e.g. `/var/lib` clear), the next outage tick re-alerts. Mitigation: this is benign — operator gets one extra DM at worst.
- **Frontend not affected:** no frontend changes this push. The user's admin dashboard verification from Wave 1 (PLAN-ADMIN-FIXES) remains the surface for visual changes.

---

## Part 12 — Open questions for user (before W1 dispatch)

1. **Schema migration:** drop-and-replace (default) vs additive (keep `auto_restart_fired_at` alongside new columns)? Default drop-and-replace because W1E column is <1 day old.
2. **Multi-image policy (W1A):** return FIRST extracted (default) vs return ALL? Default first.
3. **Healthcheck DM rate-limit (W1C):** one-per-outage (default) vs every-5-min-until-acked? Default one-per-outage.
4. **Operator step for soma-improver env backfill (S3 follow-up):** include in this push as W1D (4th subagent) OR defer? Default defer — not blocking; clean follow-up next round.
5. **`auto-restart-services.sh` self-DM scope:** every successful run vs only when called from listener-direct path? Default every successful run (operator-visibility wins).

User confirms or overrides defaults before approving dispatch.

---

## Part 13 — Relay-copy status

`files.mayankgupta.in` is LIVE (markserv installed in PLAN-ADMIN-FIXES W1F). This plan will be copied to `/var/lib/claude-soma/relay/PLAN-ARCH-NOTIFY.md` immediately after commit; user can view at `https://files.mayankgupta.in/PLAN-ARCH-NOTIFY.md` (basicauth `soma:<HERMES_FILES_PASSWORD>`).

---

## Execution log (appended by lead during execution)

_(empty — to be filled after user approval + Wave 1 dispatch)_
