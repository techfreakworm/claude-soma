# Claude Soma — Improvement Roadmap (2026-06-08)

> **Status: PROPOSED — for operator review before implementation.**
> Produced via a superpowers-style deep-planning pass (7 parallel
> read-only subsystem explorers -> synthesis -> adversarial completeness
> critic). 7 subsystems audited, 58 findings. No P0 outages surfaced;
> the highest severity is P1. Dominant theme: **drift between committed
> code and what is actually running** (deploy/systemd, schema-doc,
> test-expectation). Per the 2026-06-08 standing directive, nothing here
> is implemented yet — this is the brainstorm -> spec -> plan artifact
> for sign-off.

> **How to read this:** Section 2 is the full prioritized findings table.
> Section 3 has spec+plan sketches for every P1. The **Critic Addendum**
> at the very bottom is the adversarial pass — it corrects five things in
> the synthesis (silent-hour DM severity, a triple-counted gate-test item,
> the SSE-auth security framing, a LinkedIn-design guardrail, and Layer-B
> blast radius). **Read the Critic Addendum first if short on time** — its
> "3 highest-confidence first moves" is the recommended starting point.

---

# Claude Soma — Improvement Roadmap

## 1. Executive Summary

Claude Soma is a live, post-V1 system whose recent engagement-pipeline hardening (queue dedup-lock, target-dedup ledger, IST drip window) shipped fast but left a cluster of activation-and-contract gaps: a deploy process that pulls `/opt` but never refreshes `/etc/systemd/system` (so the IST timer fix and any future unit edit are silently inert), a `PROPOSED`-but-already-live engagement schema, unvalidated LinkedIn DOM selectors gating production posting, and a backlog of ~7 prompt/MCP changes parked behind a single operator-initiated channel restart. None of these are P0 outages, but five P1 items represent real correctness or activation risk on a production VPS. The dominant theme is **drift between committed code and what is actually running** — deploy/systemd drift, schema-doc drift, and test-expectation drift (13 failing tests, no CI). This roadmap prioritizes the deploy/systemd sync fix first (it gates the safety of every other unit change), then the engagement contract/validation P1s, the notify-and-channel correctness bugs, the dashboard/SSE auth P1s, and finally CI to stop the bleeding.

## 2. Prioritized Findings

| Priority | Subsystem | Title | Kind | Effort |
|---|---|---|---|---|
| P1 | deploy-install-systemd | Deploy script does not refresh /etc/systemd/system when systemd/ changes | gap | small |
| P1 | deploy-install-systemd | IST timer fix (d79491d) ships to /opt but does not activate in /etc | bug | small |
| P1 | deploy-install-systemd | Caddy install drops `import /etc/caddy/conf.d/*.caddyfile` line | bug | trivial |
| P1 | notify-and-channel | Systemd units are plain copies, not symlinks — deploy gap | risk | small |
| P1 | notify-and-channel | send_tg_reply bypasses FI-DM-SAFE-ATTACH attachment filtering | bug | small |
| P1 | notify-and-channel | NEEDS_INPUT delivery loses tg_msg_id correlation if DM fails | bug | medium |
| P1 | engagement-pipeline | Schema v1 PROPOSED but implementation live — contract risk | gap | small |
| P1 | engagement-pipeline | LinkedIn Layer-A DOM selectors UNVALIDATED — posting failure risk | risk | small |
| P1 | engagement-pipeline | Posted-targets ledger backfill not documented as runbook step | gap | trivial |
| P1 | orchestrator-leads-gate | Subagent agent_id exemption not validated upstream | gap | small |
| P1 | dashboard-api-frontend | CORS domain parsing inverts behavior (double soma. prefix) | bug | trivial |
| P1 | dashboard-api-frontend | discover_team called with agent_id instead of project name | bug | trivial |
| P1 | dashboard-api-frontend | EventSource cannot authenticate; SSE /events 403s in prod | gap | small |
| P1 | grok_image | subprocess.run missing check=True allows silent exit-code failures | bug | trivial |
| P1 | repo-health-tests-ci | No CI pipeline (GitHub Actions / pre-commit) | gap | small |
| P2 | engagement-pipeline | Schema producer mismatch — X legacy excerpt field disconnect | bug | small |
| P2 | engagement-pipeline | No observability on draft throughput | improvement | medium |
| P2 | engagement-pipeline | Empty-hour DM silently no-ops on missing Telegram creds | bug | small |
| P2 | engagement-pipeline | Layer-A selectors may break on LinkedIn CSS refresh (no circuit breaker) | risk | small |
| P2 | notify-and-channel | _format_milestone_dm does not truncate unbounded progress | gap | small |
| P2 | notify-and-channel | pending_inputs.question lacks length constraint | gap | small |
| P2 | notify-and-channel | 3 pre-existing orchestrator_gate test failures | bug | medium |
| P2 | notify-and-channel | 64KB payload cap not validated before storage | gap | medium |
| P2 | notify-and-channel | Orchestrator gate deny-reason template drift | techdebt | small |
| P2 | orchestrator-leads-gate | git fetch --depth=1 (equals form) not blocked | bug | trivial |
| P2 | orchestrator-leads-gate | Team member brief field assigned from role | bug | small |
| P2 | orchestrator-leads-gate | Gate test expectations outdated after shell→Python rewrite | gap | small |
| P2 | orchestrator-leads-gate | Tmux socket may orphan if systemd unit stop fails | gap | small |
| P2 | dashboard-api-frontend | discover_team mock signature mismatch (masked by bug) | bug | trivial |
| P2 | dashboard-api-frontend | gate fail-open on jq failure | bug | small |
| P2 | dashboard-api-frontend | Deny reason no longer references responsive_bot.md | gap | small |
| P2 | dashboard-api-frontend | Header case mismatch x-github-handle vs X-GitHub-Handle | gap | small |
| P2 | deploy-install-systemd | Bootstrap installs units as plain copies, no drift metadata | techdebt | medium |
| P2 | deploy-install-systemd | No repeatable deploy script for systemd-only changes | gap | small |
| P2 | deploy-install-systemd | Missing test coverage for deploy.sh systemd sync | gap | small |
| P2 | voice-and-media | voice_tts uncaught wave.Error on WAV duration read | bug | trivial |
| P2 | voice-and-media | grok_image urlretrieve without error handling | gap | small |
| P2 | voice-and-media | grok_image output_dir creation race condition | risk | small |
| P2 | voice-and-media | voice_tts WAV cleanup not guaranteed on error | gap | small |
| P2 | repo-health-tests-ci | test_bash_deny[--depth=1] flag-parsing bug | bug | trivial |
| P2 | repo-health-tests-ci | test_fail_open_broken_jq obsolete | gap | small |
| P2 | repo-health-tests-ci | test_deny_reason_mentions_responsive_bot stale | gap | small |
| P2 | repo-health-tests-ci | Mypy strictness debt (90+ errors) | techdebt | medium |
| P2 | repo-health-tests-ci | CORS test isolation failures | gap | small |
| P3 | engagement-pipeline | Ledger reconciliation not automated | improvement | small |
| P3 | orchestrator-leads-gate | RC URL polling timeout returns empty without logging | gap | small |
| P3 | orchestrator-leads-gate | Gate deny-list no drift detection for new tools | gap | trivial |
| P3 | orchestrator-leads-gate | PROJECT name length vs socket path no guard | gap | trivial |
| P3 | dashboard-api-frontend | CORS allows all methods/headers | improvement | trivial |
| P3 | dashboard-api-frontend | Usage trend shape not validated | gap | small |
| P3 | voice-and-media | grok_image no file-suffix vs content-type validation | improvement | small |
| P3 | voice-and-media | codex SIGKILL escalation untested | gap | medium |
| P3 | voice-and-media | grok_image regex excludes .gif/.bmp | improvement | trivial |
| P3 | repo-health-tests-ci | Ruff debt (108 unused imports) | techdebt | trivial |
| P3 | repo-health-tests-ci | 20 stale agent worktrees | improvement | small |
| P3 | repo-health-tests-ci | CLAUDE.md 5 days stale | improvement | trivial |
| P3 | deploy-install-systemd | bootstrap no post-condition validation for missing units | improvement | small |
| P3 | deploy-install-systemd | finalize-caddy.sh no daemon-reload | improvement | trivial |

*(No P0 items surfaced; the highest-severity findings are P1. The cluster of deploy/systemd P1s is treated as the critical path.)*

## 3. P1 Specs & Plans

### P1-A — Deploy refreshes /etc/systemd/system + activates timer fix (deploy-install-systemd, plus the notify-and-channel "plain copies" duplicate)

**SPEC.** *Problem:* `deploy.sh` rsyncs `/opt/claude-soma` and restarts only the frontend; `/etc/systemd/system/*.{service,timer}` are independent plain copies that never get refreshed, so committed unit changes (notably the FI-DRIP-IST-WINDOW timer in d79491d) are inert in production. *Desired behavior:* a deploy that changes any unit file copies the changed units to `/etc`, runs `daemon-reload`, and re-enables/restarts changed units; drift between `/opt` and `/etc` is detectable. *Acceptance:* after `deploy.sh`, `systemctl cat claude-soma-engagement-drip.timer` shows the repo's IST `OnCalendar`; a drift check prints "in sync"; an automated test proves a mutated `/opt` timer propagates to `/etc`.

**PLAN.** (1) Extract the copy+reload logic from `scripts/migrate-to-ist.sh` (lines ~115–155) into a new `scripts/deploy-systemd.sh` that copies `systemd/claude-soma-*.{service,timer}` to `/etc/systemd/system/`, runs `systemctl daemon-reload`, and `enable --now`/`restart` any unit whose content changed. (2) Call it from `deploy.sh` after the rsync, before the frontend restart. (3) Add `tests/scripts/test_deploy_systemd.sh`: mutate a temp `/opt` timer, run the sync against a fake `/etc`, assert the change landed and `daemon-reload` was invoked (shellcheck as baseline). Files: `scripts/deploy.sh`, `scripts/deploy-systemd.sh` (new), `INSTALL.md`, `tests/scripts/test_deploy_systemd.sh`. This is the **gating fix** — it must land before any further unit/timer edits, and it subsumes the notify-and-channel "plain copies" finding (consider the symlink alternative `ln -sf /opt/.../systemd/*.{service,timer} /etc/systemd/system/` documented in `INSTALL.md` as the long-term option).

### P1-B — Caddyfile preserves `import /etc/caddy/conf.d/*.caddyfile` (deploy-install-systemd)

**SPEC.** *Problem:* the repo `Caddyfile` lacks the trailing `import /etc/caddy/conf.d/*.caddyfile`; a re-bootstrap/deploy overwrites `/etc/caddy/Caddyfile` and deletes the hand-added line, breaking the `files.mayankgupta.in` relay. *Desired behavior:* install is idempotent — the import line always survives. *Acceptance:* after re-running bootstrap step 13 against a fixture, the installed Caddyfile ends with the import line and the relay site still resolves.

**PLAN.** (1) Add `import /etc/caddy/conf.d/*.caddyfile` as the last line of the repo `/opt/claude-soma/Caddyfile`. (2) Defensive: in `bootstrap.sh` (~lines 554–611) append the line after install if absent. (3) Add a tiny grep-based assertion to `smoke_install.sh`. Files: `Caddyfile`, `scripts/bootstrap.sh`, `tests/.../smoke_install.sh`. Trivial; bundle with P1-A since both touch install/deploy.

### P1-C — send_tg_reply applies FI-DM-SAFE-ATTACH filtering (notify-and-channel)

**SPEC.** *Problem:* the `send_tg_reply` MCP tool (`server.py:431-490`) attaches all listed files with no extension/path safety check; only `_format_completed_dm` runs `_classify_attachments`. *Desired behavior:* every DM attachment path is classified before upload; blocked/oversized files are reported back to the caller, not silently sent. *Acceptance:* a `send_tg_reply` call including an internal `.env`/`.db` path uploads only the sendable files and returns `{sendable:[...], blocked:[...]}`; a unit test asserts an internal path is excluded.

**PLAN.** (1) In `send_tg_reply`, route `files` through `_classify_attachments` (reuse lines 373–428) before `_tg_post_multipart`. (2) Add `sendable`/`oversized`/`blocked` keys to the response dict. (3) Test in `tests/` with a mix of user-facing and internal paths. Files: `src/.../server.py`, notify tests. Ships in the **channel-restart batch**.

### P1-D — NEEDS_INPUT keeps tg correlation when DM delivery fails (notify-and-channel)

**SPEC.** *Problem:* for the URGENT NEEDS_INPUT type, if `_send_proactive_dm` returns `None`, the event is marked `delivery_error` but `pending_inputs.tg_msg_id` is never written, so the timeout monitor later closes the input with no correlation context. *Desired behavior:* failed delivery is recorded distinctly and the pending_input row is still trackable. *Acceptance:* a forced DM failure leaves a `pending_inputs` row with an explicit `tg_delivery_failed=1` (or `tg_msg_id=0`), and the timeout loop logs the failure with the lead name.

**PLAN.** (1) Add a `tg_delivery_failed` column to `pending_inputs` (`notify_store.py`) or store `tg_msg_id=0` sentinel. (2) In `server.py:860-878`, set the flag on `msg_id is None` for URGENT types and keep the row tracked. (3) Surface the flag in `_timeout_monitor_loop` log lines. (4) Test the failure path with a mocked DM returning None. Files: `notify_store.py`, `server.py`, tests. Medium; ships with the channel-restart batch.

### P1-E — Engagement schema v1 sign-off / rollout gate (engagement-pipeline)

**SPEC.** *Problem:* `docs/engagement-schema.md` says "PROPOSED … NOT YET ACTIVE" while `engagement-hourly-drip.py:304` already pins `SCHEMA_VERSION='engagement.v1'` across the live drip/dispatch path. *Desired behavior:* the schema's status matches reality — either operator sign-off is recorded and the doc flipped to ACTIVE, or activation is gated behind an env flag. *Acceptance:* `engagement-schema.md` header reads ACTIVE with a dated sign-off reference (NEXT.md/decision log), OR the drip refuses to run v1 unless `HERMES_ENGAGEMENT_SCHEMA_V1_ENABLED` is set.

**PLAN.** (1) Confirm/record sign-off on the frozen fields (`eng-{x|li}-{6hex}` id, topic tags, `relevance_note`, footer timestamp). (2) Flip the doc header to ACTIVE with the dated decision, or add the env gate to `engagement-hourly-drip.py`. Files: `docs/engagement-schema.md`, optionally `scripts/engagement-hourly-drip.py`, `NEXT.md`. Small; pure doc-vs-code reconciliation.

### P1-F — Validate LinkedIn Layer-A DOM selectors before production posting (engagement-pipeline)

**SPEC.** *Problem:* `engagement-browse-linkedin.js` (lines 30–35, 105–138) carries an explicit "LIVE VALIDATION NEEDED" block; the Copy-link menu selectors (`button[aria-label*='control menu' i]`, `/copy link/i`) have never been verified against current LinkedIn DOM. *Desired behavior:* selectors are validated live and the validation date is recorded before the path is trusted in production. *Acceptance:* a comment block in the script records a validation date and the confirmed selectors; a manual run via the warm `playwright-linkedin` session extracts a real Copy-link URL.

**PLAN.** (1) Open social-manager's warm `playwright-linkedin` MCP session, navigate to `https://www.linkedin.com/feed/`, snapshot the post control-menu, confirm the button selector and the "Copy link" menu-item text. (2) Update selectors if drifted; stamp the validation date in the script header. (3) Pair with the P2 circuit-breaker (counter on Layer-A success rate) as a fast follow. Files: `engagement-browse-linkedin.js`. Small; requires a live LinkedIn session (manual/operator-adjacent).

### P1-G — Document posted-targets ledger backfill as a deploy runbook step (engagement-pipeline)

**SPEC.** *Problem:* FI-TARGET-DEDUP-LEDGER needs a one-time `--backfill-posted-ledger` run, but `engagement-drip.md` (lines 78–87) only documents `mkdir + touch queue.jsonl`; skipping the backfill causes silent re-posts to already-posted targets. *Desired behavior:* the operator install/deploy runbook makes the one-time backfill an explicit, ordered step. *Acceptance:* `engagement-drip.md` "Operator install" lists `python3 scripts/engagement-hourly-drip.py --backfill-posted-ledger` with a "MUST run exactly once before enabling the timer" note.

**PLAN.** (1) Add the step to `engagement-drip.md`. (2) Optional guard: the drip warns if the ledger file is empty/absent at first enable. Files: `engagement-drip.md`. Trivial — do this immediately; it protects live posting.

### P1-H — Validate subagent agent_id exemption provenance (orchestrator-leads-gate)

**SPEC.** *Problem:* `orchestrator_gate.py:61-65` exempts any PreToolUse event carrying an `agent_id` field; nothing documents or enforces that this field originates from Claude Code's official hook JSON, so a future format change or a forged field silently defeats the gate. *Desired behavior:* the exemption's trust boundary is explicit and resilient. *Acceptance:* a code comment + CLAUDE.md note state that `agent_id` is trusted only from official PreToolUse hook events; a test asserts the exemption fires only on the hook-shaped event.

**PLAN.** (1) Add the trust-boundary comment in `orchestrator_gate.py` and a CLAUDE.md line. (2) Optionally add a known-subagent allowlist hook for future diversity. (3) Add a regression test exercising the exemption path. Files: `scripts/orchestrator_gate.py`, `CLAUDE.md`, `tests/test_orchestrator_gate.py`. Small.

### P1-I — Fix CORS double-prefix bug (dashboard-api-frontend)

**SPEC.** *Problem:* `api/main.py:16-18` strips a `soma.` prefix then unconditionally re-wraps as `https://soma.{_base}`, so `SOMA_DOMAIN=dashboard.example.com` becomes `https://soma.dashboard.example.com`. *Desired behavior:* a non-`soma.` domain is used verbatim as the origin. *Acceptance:* `test_cors_default_uses_soma_domain` and `test_cors_soma_domain_env_overrides_default` pass; `SOMA_DOMAIN=dashboard.example.com` yields `https://dashboard.example.com`.

**PLAN.** (1) Only strip+re-add the prefix when it was already present; otherwise use the value directly. (2) Run the two CORS tests. Files: `src/claude_soma/api/main.py`. Trivial.

### P1-J — discover_team called with project name, not agent_id (dashboard-api-frontend / orchestrator)

**SPEC.** *Problem:* `project_orchestrator/server.py:328` calls `discover_team(p['agent_id'], ...)` but the function signature is `discover_team(name, registry_members=None)`, causing a `TypeError` (`test_project_team_returns_roster` fails). *Desired behavior:* the project name is passed. *Acceptance:* `test_project_team_returns_roster` passes; the team roster route returns members.

**PLAN.** (1) Change the call to `discover_team(name, registry_members=registry_members)`. (2) Update the test mock to `lambda name, registry_members=None: roster` (the P2 mock-signature finding, now unmasked). Files: `src/.../project_orchestrator/server.py`, `tests/api/test_projects.py`. Trivial; do both together.

### P1-K — SSE /events authentication for EventSource (dashboard-api-frontend)

**SPEC.** *Problem:* `frontend/lib/sse.ts:12` uses `new EventSource(path)` (no custom headers possible), but `routes/events.py:15` requires `require_authed_user` reading `X-GitHub-Handle`, so the activity feed 403s in production. *Desired behavior:* the SSE feed authenticates by a mechanism EventSource supports, without weakening access control. *Acceptance:* an authenticated dashboard session receives the activity stream; an unauthenticated request is rejected.

**PLAN.** Prefer option (a): remove the header-`Depends` from `/events` and rely on the Next.js middleware that already gates the route (the page is only reachable behind auth). If stronger backend gating is required, fall back to a short-lived query-param token. (1) Drop/relax the dependency in `routes/events.py`. (2) Verify `frontend/lib/sse.ts` connects. (3) Add a test that the feed yields events for an allowed caller. Files: `src/.../api/routes/events.py`, `frontend/lib/sse.ts`, tests. Small.

### P1-L — grok_image subprocess uses check=True (voice-and-media)

**SPEC.** *Problem:* `grok_image/server.py:69-74` calls `subprocess.run` without `check=True`, making the `except CalledProcessError` block unreachable and leaving only the manual `returncode` check — inconsistent with `voice_tts`. *Desired behavior:* non-zero exits raise `CalledProcessError` with captured output context. *Acceptance:* a forced non-zero exit raises a `RuntimeError`/`CalledProcessError` carrying stderr; a unit test covers it.

**PLAN.** (1) Add `check=True` to the `subprocess.run` call; remove the redundant manual returncode check. (2) Test with a stub CLI that exits non-zero. Files: `src/claude_soma/mcp_servers/grok_image/server.py`, tests. Trivial. (Note: the audit also cited a stale path `claude_servers/grok_image` for the race-condition finding — verify the canonical path `src/claude_soma/mcp_servers/grok_image/server.py` while here.)

### P1-M — Establish a CI pipeline (repo-health-tests-ci)

**SPEC.** *Problem:* no GitHub Actions / pre-commit; 13 tests are red and nothing prevents pushing broken code to `origin/main`. *Desired behavior:* every push runs pytest + ruff + mypy (mypy non-blocking initially); the suite must be green before merge. *Acceptance:* `.github/workflows/test.yml` runs on push/PR; the gate fails on a deliberately broken test; ruff/pytest are blocking, mypy advisory.

**PLAN.** (1) Land the gate-test fixes first (P2 cluster: `--depth=` parse, delete `test_fail_open_broken_jq`, fix/update `deny_reason` and CORS isolation tests) so CI starts green. (2) Add `.github/workflows/test.yml` (pytest + `ruff check`) and an optional `.pre-commit-config.yaml`. (3) Add mypy as a non-blocking job. Files: `.github/workflows/test.yml` (new), `.pre-commit-config.yaml` (new). Small; sequence after the red tests are fixed.

## 4. Sequencing

1. **Deploy/systemd critical path first — P1-A + P1-B (and the symlink decision).** This is the keystone: until `/etc` refresh works, every other unit/timer change (including the already-shipped IST fix) is silently inert. Land and verify on the live VPS before touching any other systemd unit. P2 follow-ups (deploy-systemd test coverage, repeatable systemd-only deploy script) attach directly here.
2. **Activate the IST timer (P1-B's sibling, the d79491d bug).** Once P1-A lands, re-run the deploy and confirm `systemctl cat` shows the IST `OnCalendar` — this closes the highest-visibility live-correctness gap.
3. **Engagement runbook + contract — P1-G then P1-E.** P1-G (ledger backfill doc) is trivial and protects live posting *now*; do it immediately, independent of code. P1-E (schema sign-off) is a doc/code reconciliation with no runtime risk once recorded.
4. **LinkedIn selector validation — P1-F.** Needs a live LinkedIn session; gate any reliance on Layer-A posting behind this, and chain the P2 circuit-breaker as a fast follow.
5. **Channel-restart batch.** P1-C (send_tg_reply filtering), P1-D (NEEDS_INPUT correlation), FI-DM-SAFE-ATTACH, FI-NO-ASKUSERQUESTION prompt change, and the other ~5 parked prompt/MCP edits should be staged together and shipped with a **single** operator-initiated `sudo systemctl restart claude-soma-channel.service`. Batch them to avoid repeated restarts; document the batch contents so the operator runs one restart after all are committed.
6. **Dashboard correctness — P1-I, P1-J, P1-K.** Independent of the above; P1-I and P1-J are trivial test-greening fixes (pair P1-J with its unmasked mock fix), P1-K is small. These also feed CI green-ness.
7. **Gate provenance + voice — P1-H, P1-L.** Small, isolated; can land any time.
8. **CI last among P1 — P1-M.** Land only *after* the P2 gate-test and CORS-test fixes so the pipeline starts green; otherwise CI will be red from day one and get ignored. Once green, CI prevents regression of everything above.

**Dependency notes:** P1-A gates step 2 and all future unit edits. P1-J depends on the test mock fix (do together). P1-M depends on the orchestrator_gate + CORS test fixes. The channel restart is a shared dependency for the entire prompt/MCP batch — coordinate it once.

## 5. Quick Wins

- **P1-G** — Add the one-line `--backfill-posted-ledger` step to `engagement-drip.md` (trivial; prevents live re-posts).
- **P1-B** — Append `import /etc/caddy/conf.d/*.caddyfile` to the repo Caddyfile (trivial; makes install idempotent).
- **P1-I** — Fix the CORS double-`soma.` prefix (trivial; greens 2 tests, unblocks prod origins).
- **P1-J** — One-line `discover_team(name, …)` fix + mock update (trivial; greens the team roster route).
- **P1-L** — Add `check=True` to grok_image subprocess (trivial; makes image-gen failures observable).
- **P2** — `git fetch --depth=1` equals-form gate fix (`startswith('--depth')`) — trivial, greens a failing gate test.
- **P2** — `ruff check src/ --fix` to clear 108 unused imports; delete the obsolete `test_fail_open_broken_jq`.
- **P2** — voice_tts `wave.Error` try/except on duration read (trivial; matches voice_stt, prevents TTS crashes on corrupt WAV).
- **P3** — Expand grok_image regex to include `gif|bmp`; prune the 20 stale `.claude/worktrees/` agent dirs; refresh the 5-day-stale CLAUDE.md.

---

## Critic Addendum

The roadmap is strong on the deploy/systemd critical path and correctly identifies drift as the dominant theme. But it drops several findings, mis-sequences one dependency, and proposes one item that risks regressing a deliberate design choice. Concrete corrections:

### 1. A genuinely-shipped item is re-proposed as net-new work
- **P1-D (NEEDS_INPUT correlation) is sound, but FI-DM-SAFE-ATTACH inside the "channel-restart batch" is mischaracterized.** FI-DM-SAFE-ATTACH is already **committed** (per RECENT) and only needs the restart — it is not new work. P1-C (`send_tg_reply` filtering) is the *actual* new work and is correctly new. The roadmap conflates them in step 5; keep P1-C as new, list FI-DM-SAFE-ATTACH as "already committed, restart-gated," not as something to build.

### 2. Dropped / under-weighted findings
- **P2 "Empty-hour DM silently no-ops on missing Telegram creds" (BUG-DRIP-SILENT-FAILURE) is under-weighted at P2 and absent from sequencing.** This *directly violates the silent-hour contract* the engagement pipeline was built around, and the dispatcher always `exit 0` so systemd masks it. On a live posting system this is closer to a P1 observability hole than a P2. At minimum it belongs in the engagement step (step 3) as a fast-follow to P1-G, not buried.
- **The P2 mock-signature fix (test_projects.py) is correctly paired with P1-J — good — but the roadmap never states that P1-J will turn a currently-*passing* (falsely green) test red.** The mock passes today *because* the bug masks it. Flag explicitly: fixing P1-J without the mock fix in the same commit breaks the suite. The roadmap says "do together" but should mark it a hard ordering constraint, not a nicety.
- **grok_image stale-path discrepancy is noted in P1-L as an aside but deserves its own verification line.** Two findings cite `src/claude_servers/grok_image/server.py` (race condition, line 63-64) vs the canonical `src/claude_soma/mcp_servers/grok_image/server.py`. One of these paths is wrong. This is a data-quality flag on the audit itself — resolve which path is real before editing, or a "fix" lands in a dead file.

### 3. Cross-subsystem theme not surfaced: the orchestrator_gate failures are triple-counted
The same 3 gate test failures appear as separate findings in **notify-and-channel**, **orchestrator-leads-gate**, **dashboard-api-frontend**, AND **repo-health-tests-ci** (8+ finding rows total). The roadmap scatters them across P2 buckets and P1-M's preamble without stating they are *one* fix-set. Consolidate into a single "gate test greening" work item (the `--depth=` parse fix + delete `test_fail_open_broken_jq` + reconcile `deny_reason`/`responsive_bot.md`). This is the literal precondition for P1-M and should be named once, owned once.

### 4. Sequencing error
- **P1-K (SSE auth) is not as independent as step 6 claims.** Its recommended fix (option a: drop the `Depends(require_authed_user)` and rely on Next.js middleware) is a *security posture change* on a production dashboard, not a test-greening fix like P1-I/P1-J. It should not be bundled with the trivial CORS fixes. Either keep the auth and use a short-lived query-param token, or get an explicit operator decision before weakening backend gating. Pulling auth off `/events` because "the page is behind middleware" assumes the API is never reachable directly — verify that assumption (is the FastAPI port exposed beyond Caddy?) before doing it.
- **P1-B is described as "B's sibling" in step 2 but P1-B is the Caddyfile fix; the IST timer activation is part of P1-A's acceptance.** Step 2 mislabels the timer-activation as "P1-B's sibling." The IST timer (d79491d) activating is literally P1-A's acceptance criterion (`systemctl cat` shows IST OnCalendar). There is no separate sibling — fix the cross-reference so the operator doesn't think there's a 4th unnamed item.

### 5. Design-choice regression risk to call out explicitly
- **Do NOT let P1-F or the P2 circuit-breaker drift into "automate LinkedIn harvesting in cron."** FI-ENGAGEMENT-HYBRID deliberately keeps LinkedIn Layer-A on social-manager's **warm** `playwright-linkedin` session, precisely because cold/headless LI harvesting is fragile and detection-prone. P1-F correctly says "requires a live LinkedIn session (manual/operator-adjacent)" — good — but the roadmap should add a guardrail line: the circuit-breaker (P2) and any selector validation must stay on the warm session and must never be wired into the headless cron drip. Otherwise a well-meaning follow-up will "fix" the validation by automating it and break the design.

### 6. Risk understated
- **P1-F + P2 (Layer-A selector drift) understates blast radius.** The comments say Layer-B fallback "works 60-70% of the time," meaning ~30-40% of LinkedIn drafts silently get a *wrong or missing* canonical link when Layer-A drifts — and the queue/ledger will still mark them dispatched. That is a silent-correctness failure on live posts, not just a "posting failure risk." The circuit-breaker should be elevated from P2 to a P1 fast-follow of P1-F, since without it there is no signal that 1 in 3 posts is degraded.

### Net assessment
Not complete — the items above (silent-hour DM severity, gate-test consolidation, P1-K security framing, LI design guardrail, Layer-B blast radius) should be folded in. The deploy/systemd critical path and the quick-wins list are well-judged and need no change.

**3 highest-confidence first moves (all trivial, zero-restart, zero-regression):**
1. **P1-G** — add the `--backfill-posted-ledger` runbook step to `engagement-drip.md` (protects live posting *now*).
2. **P1-A + P1-B together** — the deploy/systemd `/etc` refresh + Caddyfile import line; this is the keystone that makes the already-shipped IST timer actually active and unblocks every future unit edit.
3. **Gate-test greening as one item** (`--depth=` parse + delete obsolete jq test + reconcile deny-reason) — the single precondition for P1-M, currently triple-counted across four subsystems.
