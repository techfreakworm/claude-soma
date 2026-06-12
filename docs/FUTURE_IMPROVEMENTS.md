# Future Improvements — Claude Soma roadmap

Consolidated, prioritized backlog of every future-improvement item, organized by theme.
Status: planning (awaiting approval). Date: 2026-05-27.

This is a **living roadmap**. It supersedes the scattered "V1.5 / coming-in-V2" notes in
`NEXT.md`, `CLAUDE.md`, `docs/CHECKLIST.md`, and the engineering notes under `docs/notes/`.
Where docs and code disagree, the code + `git log` win (per repo memory); items below were
reconciled against the live tree on 2026-05-27.

For *bugs and fragilities* (as opposed to net-new features), see
[`KNOWN_BUGS.md`](KNOWN_BUGS.md). A few entries here cross-reference it where an improvement
is really "harden an existing fragility."

Effort key: **S** = hours/≤1 day · **M** = a few days · **L** = a week+.

---

## Recently shipped (context)

So the future list below isn't confused with finished work. Summarized from `git log`
(newest first) and the operations checklist; all on `origin/main` unless noted.

| Area | What landed | Commit(s) |
|---|---|---|
| Orchestration | Project-leads inherit all MCPs + plugins **except** telegram (via `user,project,local` + curated `lead-mcp.json`) | `6ac7ed2` |
| Orchestration | Each lead runs in its **own cgroup** via `sudo systemd-run` (channel restart can't kill leads) | `ae7d7be`, `346af89` |
| Orchestration | Async orchestrator↔lead reply-polling dispatched to background agents | `b205642` |
| Orchestration | Registry liveness reconciliation (`dead` vs `killed`); ghost leads stop counting against the cap | `a974011` |
| Orchestration | Spawner rewrite: tmux-wrapped per-project sessions (replaced removed `claude --bg`); `--` guards the brief | `d31df9a`, `659fed6` |
| Orchestration | Per-lead pane logging to `/var/log/claude-soma/<name>.log` | `a989991` |
| Orchestration | Capture `claude.ai/code` Remote Control URL (not just legacy `rc.claude.com`) | `c2a59e8` |
| Channels | Telegram poller-hijack hardening: telegram moved out of user scope, bot opts in via `--settings`; `claude-safe` wrapper; in-pane healthcheck recovery | `4082e3b`, `d087330`, `ca758d0`, `1a9c89b` |
| Voice | STT switched to `base.en` (English-only, ~13× faster: ~9 s vs ~121 s); transcript always echoed to the user | `3322e96`, `cc7ec8b` |
| Social | Shared persistent Playwright auth (per-platform `storageState` + weekly `pw-refresh`); 5 authenticated playwright MCP servers | `45b600a`, `920f506` |
| Social | `social-publish` skill + per-platform writer/poster agents (X thread, X Article, LinkedIn, Medium) | (skills tree) |
| Dashboard | `/api/routines` aggregates registry + all systemd timers + cron + cloud; slow cloud query cached + parallelized | `9a76a75` |
| Dashboard | Fixed unstyled dashboard (standalone static-asset copy wired into deploy) | `7f7b729`, `9542e98` |
| Dashboard | Admin graph shows reconciled status + agent-team teammates | `ce29862`, `1920bde` |
| Dashboard | Admin logs **tool filter** (`?tool=` on `/api/logs`) — *was a "V1.5" item; now DONE* | `logs.py` |
| Packaging | `somux` helper shipped in-repo + installed by bootstrap; bootstrap now also installs piper/whisper/ngrok/docker/playwright | `c428fd6`, `8e59914` |
| Reliability | Registry sqlite connection made thread-safe | `44e2c66` |
| Orchestration | Project-leads given the Hugging Face MCP (docs search + Hub) | `f65527f` |
| Channels | Channel resumes its prior session on restart via `--continue` | `c33fe5e` |
| Orchestration | `kill_project_impl` post-kill liveness verification + one retry; raises `RuntimeError` if lead survives both attempts (prevents zombie rows masking as `killed`) | `c99c743` |
| Orchestration | Reaper tmux-liveness check: `is_lead_alive()` prevents hibernating a lead whose `last_activity` column is stale but whose process is still running | `53f7113` |
| Orchestration | Orchestrator gate (`scripts/orchestrator_gate.sh`): deny-list for network shell-outs and production-path file edits in Bash/Write tool calls routed through the bot | `9b26c72` |

---

## Priority summary (top of the backlog)

| # | Item | Theme | Effort | Why it's near the top |
|---|---|---|---|---|
| 1 | ~~Wire `kill_session()` into `kill_project_impl`~~ **DONE** (`238f78f`) | Orchestration | S | Killing a project leaves the tmux/cgroup alive (registry-only `killed`). Correctness gap. |
| 2 | Bot-side `register_routine()` calls | Observability | M | Routines table exists but nothing populates it; `created_by` never canonical. |
| 3 | Fix Phase-1 bootstrap iptables ordering into `vps_bootstrap.sh`/wizard | Packaging | S | Known footgun: ACCEPT must precede Oracle's REJECT (pos 5, not 6). |
| 4 | Killed-lead resume (`--session-id`/`--resume`) | Orchestration | M/L | A dead lead loses all context today. See KNOWN_BUGS #2. |
| 5 | Demo video on landing page (replace placeholder) | Dashboard/Social | M | **IN PROGRESS** via `social-publish`. Last visible V1.5 ship-blocker. |
| 6 | ~~Logrotate for `/var/log/claude-soma/*.log`~~ **DONE** | Observability | S | Per-lead + channel logs grow unbounded. |
| 7 | ~~`NEEDS_REAUTH-<platform>` surfacing to the user~~ **DONE** | Social | S | Playwright auth silently rots; user only finds out when a post fails. |

### Surfaced 2026-05-29 / 2026-05-30 / 2026-05-31

Not yet prioritized above the existing top-7; tracked here for visibility. The first two are **S**; the lead notify channel is **High** priority, **M** effort.

- **SCHEDULE-ROUTINE bash-fallback** — the working one-shot Telegram reminder path (`nohup setsid bash` + Bot-API `sendMessage`) is tribal knowledge; codifying it in the skill prevents reinvention. `RemoteTrigger` v2 is stale per memory (HTTP 400). Validated T4, 2026-05-29. See Orchestration section.
- **Orchestrator gate false positives** — `9b26c72` gate flags heredoc bodies that *mention* network tools (not invoke them) and `/tmp` writes. A few hours of heuristic tightening eliminates daily false-positive friction. Validated by two live bot denials, 2026-05-29. See Orchestration section.
- **Lead → orchestrator notify channel** (**High**) — single biggest UX friction in the current system; every other lead interaction is gated on user-side polling. Leads have no way to push a completion event back to the bot — the bot must `capture-pane` each lead on every `Status?` ping. Full mechanism survey + recommendation (localhost HTTP API + SQLite spool) in the Orchestration section below. Surfaced 2026-05-30.
- **Caddy-via-own-domain file relay** (**Medium-High**) — ngrok bandwidth pain hit during 235 MB pptx demo relay, 2026-05-30; replaces markserv+ngrok with a Caddy-routed `/files/` path on the user's own domain. Full design + recommendation in the Social section below.
- **Default notify-event emission by leads** (**High**) — today's FI-DOMAIN implementation pass shipped cleanly but soma-improver fired zero STARTED / MILESTONE / NEEDS_INPUT events; user had to poll to know progress. FI-NOTIFY ships the channel (`c348675`); this follow-up bakes the convention into every lead brief at spawn time so leads are producers of progress signals by default. Score 25 (P1 × leverage 5 / effort S) — highest in the open queue. Details in the Orchestration section below. Surfaced 2026-05-31.
- **Lead-level parallelism** (**Medium-High**, operator-requested, effort **L**) — each lead processes operator tasks serially today; "do these 5 things" runs one-at-a-time. Desired: a lead fans out throwaway parallel teammates, tears them down on completion, and retains the full task ledger. Load-bearing constraint: shared stateful MCP clashes — **Playwright especially** (one shared browser/`storageState` per platform; two workers driving `playwright-x` collide) — so the design needs task-division by contention class, a lease/scheduler, concurrency caps, and clean teardown. Full write-up (problem, desired behavior, mitigation options, open questions) in the Orchestration section below. Capture-only; not yet scoped. Surfaced 2026-06-12.

---

## Channels

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| Discord channel | README claims "Telegram, Discord, custom"; only Telegram is live. Add a Discord channel-plugin opt-in mirroring the telegram `--settings` pattern. | M | telegram scope-isolation pattern (done) |
| Generic "custom" channel doc | Document how to add an arbitrary Claude Code channel plugin given the poller-hijack constraints (`enabledPlugins` must be in a loaded scope; isolate via `--settings`). | S | KNOWN_BUGS #1 fully closed |
| Subagent-vector close-out for poller hijack | The bot's own Agent/Task dispatches are the last uncovered hijack vector. Either verify `--settings` is **not** inherited by subagents, or route heavy work to orchestrator leads. | M | maintenance window; KNOWN_BUGS #1 |
| Inbound media beyond voice | Handle photos/docs sent to the bot (currently only `.oga` voice is special-cased). | M | — |

## Voice

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| Multilingual STT toggle | Ship `base.en` as default (done) but expose `HERMES_WHISPER_MODEL=large-v3-turbo` as a documented opt-in for non-English. Note: bootstrap still *builds* `large-v3-turbo` while `.mcp.json` points at `base.en` — reconcile (see KNOWN_BUGS). | S | model-path reconciliation |
| Voice-intake hook rewrite | `scripts/voice_intake.sh` is a silent no-op; the original `UserPromptSubmit` schema was rejected by Claude Code 2.1.150. Rewrite against the new schema *if a real need emerges* (voice routing works without it today). | M | only if needed |
| Voice selection per-user | Single hard-coded `en_US-ryan-medium` piper voice. Allow per-user/per-routine voice choice. | S | — |
| Streaming / faster TTS | piper→opus is fine (~4 s round-trip) but a longer reply blocks; consider chunked synthesis. | M | — |

## Orchestration

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| ~~Wire `kill_session()` into `kill_project_impl`~~ **DONE** (`238f78f`) | Spawner has `kill_session()` (stops the unit + tmux); `kill_project_impl` now calls it. | S | — |
| **Killed-lead resume** | Respawn a dead-but-not-retired lead with `--session-id`/`--resume` so it keeps history. Team teammates are the hard part (ephemeral; recommend v1 = lead re-dispatches). See KNOWN_BUGS #2. | M/L | liveness reconciliation (done) |
| Team-roster persistence | Persist teammate names+briefs at dispatch so a resumed lead can re-establish its team. | M | killed-lead resume |
| Exact teammate handles in graph | `discover_team()` is coarse (pane-derived `teammate-N`, no `@ping` handle). Have leads self-report into a registry table. | M | — |
| Reaper ↔ resume integration | `scripts/reaper.py` hibernates idle leads at 24 h / hard-deletes at 7 d; integrate with resume so hibernated leads can wake with state. | M | killed-lead resume |
| Concurrency-cap tuning | `HERMES_MAX_CONCURRENT_PROJECTS=6` is a static guess; make it memory-aware. | S | — |
| **Concurrency-cap accounting (defensive)** | Intersect the active-row count with actual tmux liveness (`is_lead_alive`) before checking against the cap, so a stale registry row above the cap doesn't permanently block new spawns. With `c99c743` + reaper fix `53f7113` drift should be rare; this is belt-and-suspenders. Low priority. | S | `c99c743`, `53f7113` |
| **`last_activity` column not bumped by tmux send-keys** | `last_activity` is only updated at spawn time and by `send_to_project_impl` (the MCP path). Raw `tmux send-keys` (the bot's canonical chat path) never touches it, so leads appear idle even when the bot has been actively talking to them — misleading for the daily-status digest and admin "idle for X" labels. Add a tiny hook to update the column on each tmux send-keys message routed. Validated today, 2026-05-29 (T3): `t1-spawn-test` `last_activity` stayed at spawn timestamp despite a successfully delivered + answered message. | S | — |
| **`kill_project` archive logs nothing for no-memory leads** | `archive=True` silently skips when the lead has no `.claude/` memory dir (e.g., throwaway test leads). Log "nothing to archive" so a caller can distinguish "archived" from "skipped silently." Trivial. | S | — |
| **Codify the SCHEDULE-ROUTINE bash-fallback pattern** | The working one-shot Telegram reminder path — `nohup setsid bash` + `sleep` + sourcing the bot token from `~/.claude/channels/telegram/.env` + a Bot-API `sendMessage` — is tribal knowledge. Wrap it into the `schedule-routine` skill / `hermes-api` MCP so it is discoverable and not reinvented per session. `RemoteTrigger` v2 is stale per memory (HTTP 400 on the `{name, cron, prompt}` body shape). Validated today, 2026-05-29 (T4 acceptance: fired on schedule, exit 0). | S | — |
| **Orchestrator gate false positives (heuristic tightening)** | Follow-up to `9b26c72`. Two false-positive patterns hit today: (a) a Bash heredoc whose body *describes* network tools was flagged as a network shell-out; (b) a `Write` call to `/tmp` was denied. Fixes: (a) match on the first non-pipeline token of the Bash command (the actual invocation), not `grep -qE` over the full command string including quoted bodies; (b) scope the Write deny to production paths (`/opt/claude-soma/` and similar), allowing `/tmp` scratch writes. Cross-reference `scripts/orchestrator_gate.sh`. | S | `9b26c72` |

### Lead → orchestrator notify channel

**Priority: High. Effort: M.**

#### Problem

Leads currently have no way to push a completion notification back to the orchestrator (the bot). The only signaling direction today is bot→lead via `tmux send-keys`. The reverse does not exist because leads cannot be given Telegram MCP access — only one process can poll `getUpdates` per bot token; a lead claiming the poller crashes the channel.

Result: the user must keep pinging the bot with `Status?` on every lead. The bot must `capture-pane` every time to learn what each lead has done. Friction is real — observed roughly eight times on 2026-05-30 alone.

Design goal: a one-way lead→orchestrator notification channel that does NOT require Telegram MCP on the lead.

#### Mechanism survey

Four candidate mechanisms scored against six criteria:

| Criterion | (1) inotify / systemd .path | (2) SQLite event table | (3) Localhost HTTP API | (4) Named pipe (FIFO) |
|---|---|---|---|---|
| **Durability across restarts** | Good — events persist on disk; the .path unit must be a sibling cgroup, not inside the channel's cgroup, or it dies on channel restart too | Excellent — unread rows survive indefinitely; bot drains on startup | Poor — a POST while the server is down is silently lost unless the caller adds retry | Very poor — a write blocks until the reader is present; events are gone if the reader is not up |
| **Latency** | Good — inotify is instant but systemd .service activation adds ~1–2 s | Fair — bounded by the poll interval (configurable; 5 s is reasonable) | Excellent — POST round-trip is <1 s; `_tg_post_json` adds another ~300 ms | Excellent — in-kernel pipe read is microseconds, BUT only while the reader is alive |
| **Ordering guarantees** | Fair — per-lead ordering is preserved; rapid cross-lead events may be coalesced by systemd | Excellent — autoincrement rowid gives strict insertion order across all leads | Good — concurrent POSTs from multiple leads can interleave at the socket layer; ordering within a single lead is preserved | Fair — atomic up to `PIPE_BUF` (4 096 B on Linux); larger payloads from concurrent leads interleave |
| **Fault isolation** | Excellent — each lead writes its own file; one lead's file does not block another's | Excellent — a crashed INSERT rolls back cleanly (WAL mode); one lead spamming events does not stall others | Good — stateless POST handler; slow leads queue at the socket but do not block each other (asyncio server) | Fair — a stalled reader stalls all writers after the pipe buffer fills; reader is a single point of failure |
| **Discoverability** | Good — convention-based path `/tmp/lead-events/<name>/event.txt`; orchestrator injects it at spawn or leads construct from their own name | Excellent — registry.sqlite is already a well-known path; leads could reuse the same DB file with a new table | Good — port must be known; trivially solved with `HERMES_NOTIFY_PORT` env var injected at spawn time | Excellent — fixed path `/tmp/lead-bus.fifo`; nothing to discover |
| **Schema-friendliness** | Excellent — file content is arbitrary JSON | Excellent — structured columns + JSON blob; queryable by type and lead name | Excellent — POST body is arbitrary JSON; handler validates and routes by `type` field | Excellent — newline-delimited JSON lines; requires payloads under `PIPE_BUF` to avoid interleaving |

**Named pipes** are eliminated: durability is fatally absent and the reader being a single point of failure violates fault-isolation.

**inotify alone** is viable but operationally fragile: a second independent systemd unit (the .path unit) is required to survive channel restarts; systemd event coalescing may swallow rapid back-to-back events from the same lead; the 1–2 s activation latency is the worst of the durable options.

**SQLite alone** has excellent durability and ordering but poll-bound latency. For a user waiting on a "completed" DM in real time, N-second polling lag is observable friction.

**Localhost HTTP API + SQLite spool** captures the best of both: sub-second delivery when the bot is up, zero event loss when it is not.

#### Recommendation

**Primary: localhost HTTP API (`POST http://127.0.0.1:${HERMES_NOTIFY_PORT}/notify`). Durability fallback: SQLite event spool (new `lead_events` table in `/opt/claude-soma/registry.sqlite`).**

Every lead call writes the event to `lead_events` first (guaranteed persistence regardless of server state), then POSTs to the HTTP endpoint for immediate delivery; on bot restart, hermes_api drains any undelivered rows. This is the only combination that is both sub-second in the normal case and lossless across restarts — the two properties that directly address the user's friction. Named pipes and inotify both fail on durability or require an additional fragile systemd unit; SQLite alone accepts up to N seconds of delivery lag.

**On the MCP tool wrapper hint:** Yes, this fits the recommended mechanism exactly. A small `hermes-notify` MCP server — a single `notify_orchestrator(type, name, payload)` tool — can be added to `lead-mcp.json`. Its only capability is writing to `lead_events` and POSTing to the notify endpoint; it has no spawn, kill, or registry read access. Because the control-plane servers (`hermes-api`, `project-orchestrator`) remain excluded from lead scope per the existing `LEAD_MCP_CONFIG_DEFAULT`, adding a read-only notify shim does not expand leads' blast radius. Leads call the tool by name; the implementation detail (HTTP vs SQLite) is fully encapsulated server-side.

#### Event schema

The bot routes events differently in the user's DM depending on the `type` field. All events are stored in the `lead_events` table with a server-side `id` (autoincrement) and `delivered_at` timestamp added on insert; leads do not need to supply these.

| Type | Required fields | Optional fields | Bot's DM treatment |
|---|---|---|---|
| `STARTED` | `name` (lead name), `description` (what it is doing) | `eta` (human-readable estimate) | One-line DM: "`<name>` started: `<description>`" |
| `MILESTONE` | `name`, `progress` (human-readable description of progress) | `percent` (0–100 integer), `eta_remaining` (human-readable) | Progress nudge DM; throttled to at most one per lead per 5 minutes to prevent spam |
| `COMPLETED` | `name`, `summary` (what was done and what was produced) | `paths[]` (absolute local file paths to deliverables), `urls[]` (links — GitHub, deployed pages, etc.) | Celebratory DM; if `paths[]` is non-empty, files are attached via the existing `send_tg_reply` multipart path |
| `NEEDS_INPUT` | `name`, `question` (the question the lead is blocked on) | `options[]` (candidate answers for a multiple-choice prompt), `timeout` (seconds before the lead proceeds with a default or aborts) | DM the question to the user; the user's next reply is routed back to the lead via `tmux send-keys`; the bot must maintain a `pending_input_for_lead` map to correlate the reply correctly |
| `ERROR` | `name`, `error` (error message), `context` (what the lead was attempting when the error occurred) | `traceback` (full stack trace), `recoverable` (bool — `true` if the lead is retrying, `false` if it has stopped) | DM with severity-tagged HTML formatting (bold lead name + `ERROR` label); `recoverable: false` triggers an additional "lead has stopped — manual intervention may be needed" suffix |

### Default notify-event emission by leads (follow-up to FI-NOTIFY)

**Priority: High. Effort: S.**

#### Problem

> "leads should EMIT NOTIFY EVENTS AT MILESTONES BY DEFAULT, not just when explicitly told to. Today you did the work fine but did not emit STARTED/MILESTONE/COMPLETED/NEEDS_INPUT events because your task brief did not say to. Result: user had to poll to know if you were stuck. Fix: bake into the lead system prompt template a convention to fire notify events at major boundaries — STARTED when a major task begins, MILESTONE for major commits, COMPLETED with deliverable paths when a task wraps, NEEDS_INPUT when blocked on the user, ERROR on hard failures. This makes the lead the producer of progress signals by default + drastically reduces user-side polling."

Today's concrete example: lead soma-improver had `mcp__hermes-notify__notify_orchestrator` available post-restart but emitted zero events during the FI-DOMAIN implementation pass. The user had to send a "are you working?" ping to learn whether the lead was stuck or still running. The channel exists (`c348675`); the muscle memory does not.

#### Recommended fix path

**Option (b): the bot's spawn flow prepends a "Standing notify convention" block to every brief** — programmatically, in `src/claude_soma/mcp_servers/project_orchestrator/server.py` (the `spawn_project_impl` function, before passing `brief` to `spawn_background_lead`).

Reasoning:
- Survives lead restarts: the prepended convention is the first text in the lead's transcript; `--continue` picks it up on every restart from the same transcript.
- Operator can override per-spawn: the brief is the operator's (or bot's) to compose; a different first block takes precedence.
- No coordinated system-prompt file deployment: option (c) below requires a new file at `/opt/claude-soma/` and a spawner `claude_argv` change; option (b) is a single-site change in `server.py`.
- Decoupled from `responsive_bot.md`: option (a) relies on the bot's context window carrying a reminder instruction each time it composes a brief, which is weaker than a code-level guarantee.

**Alternative (a): Add a standing block to `system_prompts/responsive_bot.md`** instructing the bot to always prepend the notify convention to each spawn brief. Trade-off: depends on the bot's inference at brief-composition time; may be skipped in low-effort mode or when the brief construction path is indirect; no code-level guarantee.

**Alternative (c): New file `system_prompts/lead_notify_convention.md` appended via `--append-system-prompt-file`** at lead spawn (new flag in `spawner.py`'s `claude_argv`). Trade-off: clean abstraction but requires (i) a new file deployed to `/opt/claude-soma/`; (ii) a spawner code change to add the flag; (iii) verification that `--append-system-prompt-file` is honoured on the Claude Code version in use. Option (b) achieves the same guarantee with fewer moving parts.

#### Convention spec

The following block is the exact text to prepend to every lead brief. The implementation subagent should insert it verbatim into `spawn_project_impl` before the `brief` argument is passed to `spawn_background_lead`:

```
== Standing notify convention (mandatory) ==
You have `mcp__hermes-notify__notify_orchestrator` available. Emit events at
these boundaries — no explicit instruction needed:

- STARTED: fire once when you pick up a substantive task (>2 tool calls of work
  expected). Payload: {"description": "<one-line summary>", "eta": "<optional>"}.

- MILESTONE: fire after each commit and after each major sub-task completes.
  Payload: {"progress": "<one-line>", "percent": <optional 0-100>, "eta_remaining":
  "<optional>"}. The listener throttles to one per lead per 5 min; be selective
  regardless — one per commit, not one per Edit.

- COMPLETED: fire when the work is shipped, tests pass, and the push lands.
  Payload: {"summary": "<2-3 sentences>", "paths": ["<absolute deliverable paths>"],
  "urls": ["<repo links>"]}. Terminal — only fire when genuinely done.

- NEEDS_INPUT: fire when you are blocked on the user (DNS, password, decision).
  Payload: {"question": "<the ask>", "options": ["<choice 1>", ...] (omit if
  open-ended), "timeout": <seconds> (omit if indefinite)}. This is the ONLY
  correct way to signal blocked-on-human; do not stall silently.

- ERROR: fire on hard failure (test broke, push failed, STOP-AND-SURFACE fired).
  Payload: {"error": "<short msg>", "context": "<what was being attempted>",
  "traceback": "<optional>", "recoverable": <bool>}.
== End notify convention ==
```

#### Anti-patterns to avoid

The user is the receiver of these DMs. Do not spam.

- **One STARTED per session** — do not fire it for every Bash call or tool invocation; once per substantive task is sufficient.
- **MILESTONE is selective** — the listener throttles server-side at 5 min per lead, but the lead should still be choosy: one per commit or major sub-task, not one per Edit call.
- **COMPLETED is terminal** — only fire when the work is genuinely done; a premature COMPLETED followed by more tool calls confuses the user about what was actually delivered.
- **NEEDS_INPUT replaces stalling** — do not silently pause waiting on a human decision; fire NEEDS_INPUT, which routes the question to the user's DM and allows the bot to correlate the reply back to the lead via `tmux send-keys`.

#### Cross-references

- `### Lead → orchestrator notify channel` (above) — the shipped mechanism this convention builds on. Commits: `c348675` (FI-NOTIFY shipped), `05f97a7` (DM HTML fix), `1502e93` (attachment hardening). Awaiting restart activation as of 2026-05-31.
- `BUGS_PLAN.md` inventory — FI-NOTIFY listed as "shipped; awaiting restart activation." This entry is the follow-up that converts the shipped channel into default usage. Score 25 (P1_weight 5 × leverage 5 / effort S 1 = 25) places it above every currently open item in the queue (previous high: FI-GATE at 20).
- `KNOWN_BUGS.md` — no directly related entry; this is a usage-convention gap, not a code defect.

#### What is NOT in this entry

`responsive_bot.md` edits, changes to `spawner.py` or `spawn_background_lead`, and any lead-template file changes are out of scope here. The actual implementation — prepending the convention block in `server.py`'s `spawn_project_impl` — is a separate impl task for the round that ships this. This entry is doc-only.

### Lead-level parallelism (concurrent task execution per project-lead)

**Priority: Medium-High (operator-requested). Effort: L. Surfaced 2026-06-12. Status: captured for later — NOT scoped to a build round yet.**

**Problem.** Each project-lead (social-manager, general-worker, algo-trader, propark-manager, any) processes operator tasks **serially**. Messages queue and the lead works one task at a time; when the operator asks a lead to do five things, they happen one after another. The lead is a single Claude session draining its own inbox in order — there is no intra-lead concurrency today. (Note: the orchestrator already runs *different leads* in parallel — each is its own cgroup'd tmux session — and any lead can already dispatch `Agent`/`Task` subagents; what is missing is a first-class, managed, teardown-aware way for ONE lead to run SEVERAL operator tasks at once with a retained ledger.)

**Desired behavior.** A lead should be able to spin up multiple **throwaway** teammates / parallel sub-agents (an agent team / split-pane) to work several tasks **concurrently**, then dispose of those teammates once each task is done and the operator confirms. Crucially, the **main lead remains the orchestrator of its own team and retains the full record**: which tasks were requested, which are done vs not, which teammates were spun up vs not, and it folds each completed teammate's result back into its own context/memory before teardown. Operator's example: *"social-manager, do these 5 things"* → the lead fans out ~5 teammates, runs them in parallel, tears them down on completion, and still knows the complete task ledger + outcomes.

**HARD CONSTRAINT — resource / MCP clashes (the load-bearing design problem).** Parallel teammates can collide on **shared stateful MCP servers**. The worst offender is **Playwright**: the authenticated playwright MCPs (`playwright-x`, `playwright-linkedin`, `playwright-medium`, `playwright-x-article`, plus the base `playwright`) each drive a **single shared browser session / `storageState` profile** (see "Shared persistent Playwright auth", `45b600a`/`920f506`). Two teammates driving `playwright-x` at once will clash — interleaved navigations, one stealing the other's page/tab, `storageState` write races, and tripping the platform's bot-detection (already a live fragility — the X/LinkedIn engagement layer fights this). Other singleton/stateful MCPs likely have the same hazard: `grok-image`/codex image-gen (one CLI session, GPU/credit contention), `voice-stt`/`voice-tts` (single binary, CPU-bound), and anything writing a shared SQLite (`registry.sqlite`, the engagement `queue.jsonl`, the social token vault). Read-only or stateless MCPs (web fetch, HF lookups) parallelize fine.

So the design MUST include:
- **(a) Intelligent task division by the lead** so parallel workers don't contend for the same MCP/browser. The lead classifies each task by which contended resource it needs and routes accordingly: e.g. serialize *all* `playwright-x` work onto one worker (or one lane), while non-Playwright tasks (drafting, research, file work) fan out freely. A per-MCP "contention class" map (Playwright-per-platform = exclusive; image-gen = exclusive; voice = exclusive; stateless = free) drives the split.
- **(b) A lock / lease / scheduler** so only one agent uses a contended resource at a time. Options to evaluate: a lightweight lease table (reuse the `fcntl.flock` + SQLite-lease pattern already proven for the engagement `queue.jsonl` write-lock) keyed by resource class; or a per-resource single-worker "lane" (a long-lived serializing worker that owns the browser and others enqueue jobs to it); or giving each worker its **own browser context/profile** (separate `storageState` copy + separate user-data-dir) so Playwright parallelism becomes safe — but that multiplies auth-refresh surface and re-auth/bot-detection risk, so it is not free.
- **(c) Sensible concurrency caps** — a per-lead max-teammate ceiling (and a global one across all leads) so a "do 20 things" request can't fork-bomb the box; cgroup/memory limits per teammate; backpressure when the cap is hit (queue the overflow, don't spawn). Tie into the existing registry liveness/cap accounting (`a974011`).
- **(d) Clean teardown that returns results to the lead** — each teammate reports its result back (the existing `Agent` final-message-as-result, or the notify channel), the lead folds it into its task ledger + memory, confirms with the operator, then disposes the teammate (kill the tmux/cgroup, free the lease). No orphaned sessions; the registry must reconcile teardown the way it reconciles dead/killed leads today.

**Mitigation options to weigh (for the build round).**
1. *Serialize-the-contended, parallelize-the-rest* (simplest viable): one exclusive lane per contended MCP class + a free pool for stateless work. Lowest risk; gets most of the parallelism benefit for the common "5 mixed tasks" case.
2. *Per-worker browser profiles* for true Playwright parallelism: isolates sessions but multiplies the auth/refresh + bot-detection surface; probably only worth it if Playwright-heavy parallel demand is real.
3. *Job-queue + lease table* (most general): every task declares its resource needs; a tiny scheduler in the lead grants leases and runs up to the cap. Most engineering; best ceiling.

The agent-teams substrate likely already exists in part (the dashboard already renders "agent-team teammates", `ce29862`/`1920bde`, and `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` is set per-lead by the spawner) — a build round should first inventory what the teams primitive gives us before building a bespoke scheduler.

**Open design questions.**
- Is the lead's task ledger persisted (survives a lead `--continue` restart) or in-context only? If teammates outlive a lead restart, the ledger + lease state must be durable (SQLite), not just in the lead's transcript.
- Teammate spawn mechanism: native Claude Code agent-teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) vs the project-orchestrator spawner vs plain `Agent`/`Task` subagents — which gives managed teardown + a result handoff + cap accounting with the least new machinery?
- How does the operator confirm completion per-task before teardown — explicit per-task ack, or auto-teardown on COMPLETED with a retained record the operator can audit later?
- Resource-class registry: where does the "which MCP is contended/exclusive" map live, and how is it kept correct as MCPs are added (a new stateful MCP must declare its contention class or default to exclusive)?
- Interaction with the existing per-lead cgroup isolation + the orchestrator gate (does a teammate inherit the lead's gate + MCP scope?).
- Failure semantics: if one teammate dies mid-task, does the lead retry, re-lease, or surface NEEDS_INPUT? Partial-completion reporting back to the operator.

**Cross-references.** Shared Playwright auth fragility: `45b600a`, `920f506` + the engagement-layer bot-detection notes. Write-lock pattern to reuse for leases: the engagement `queue.jsonl` `fcntl.flock` + SQLite design (FI-QUEUE-DEDUP-LOCK). Registry liveness/teardown reconciliation: `a974011`. Agent-team teammates already surfaced in the dashboard: `ce29862`, `1920bde`. This entry is **capture-only** — no implementation, no scoped build round yet.

## Dashboard

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| **Demo video** | Replace `frontend/components/landing/DemoVideo.tsx` `[demo video placeholder]` with the real 60-s build-log video. **IN PROGRESS** (social-publish campaign). | M | video asset |
| Routines cloud-cache prewarm | First request per TTL still pays ~12 s for the cloud `claude -p` query. Extend the cache-refresh timer to prewarm so no user waits. | S | — |
| Landing live-stats polish | Hero + live `/api/public/stats` exist; polish copy/screenshot for the showcase. | S | — |
| Per-lead log viewer in admin | Surface `/var/log/claude-soma/<name>.log` (ANSI-stripped) in the admin UI for crash forensics. | M | logrotate; ANSI strip |
| Hard-coded domain/handle cleanup | `claude.mayankgupta.in` vs `soma.mayankgupta.in` drift across `api/main.py`, `wizard/init.py`, Caddyfile, units. Centralize (ties into the install config layer). | S/M | MULTI_PLATFORM_INSTALL config layer |
| **Admin file dropper for large uploads (>20 MB)** | User tried to send a 235 MB pptx to `ppt-manager` via Telegram; the `getFile` endpoint rejected it ("file is too big", ~20 MB cap); file had to be `scp`'d manually and copied into the lead inbox by hand — recurring pattern. Design: drag-drop zone on the per-lead admin page; files land in `/home/ubuntu/projects/<lead-name>/decks/inbox/` (or a generic watched inbox); authenticated via the existing GitHub-handle gate; multipart streaming so 200+ MB doesn't OOM the API; manifest alongside each upload (`name`, `size`, `sha256`, `uploaded_at`); optional follow-up DM ping to the user when a file lands so they can immediately brief the lead. | M | KNOWN_BUGS #10 (same incident; the dropper sidesteps the cap entirely while the bug fix stops the channel stall) |

## Social

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| ~~`NEEDS_REAUTH` surfacing~~ **DONE** (healthcheck.sh extended to DM via broadcast.jsonl, dedupe per-platform per-day) | `pw-refresh.js` drops a `~/.claude-pw/NEEDS_REAUTH-<platform>` sentinel when a session dies; today only the journal shows it. | S | — |
| Routines-cache prewarm for posts | (shared with dashboard) keep playwright sessions warm so a scheduled post never hits a cold login wall. | S | — |
| More platforms | Writer/poster agents exist for X (thread + Article), LinkedIn, Medium. Candidates: Bluesky, Mastodon, Threads. | M each | shared-auth pattern |

### Caddy-via-own-domain file relay (replace markserv+ngrok)

**Priority: Medium-High. Effort: M.**

#### Problem

The system currently uses `ngrok` for ad-hoc file relay: the `social-publish` pipeline and
similar workflows serve large files (images, pptx decks, video clips) via `markserv + ngrok`
(`https://51b26900ecba.ngrok.app/` and similar). This works but has real operational costs:
ngrok's free tier has bandwidth ceilings, adds a hop through Cloudflare's infrastructure, and
presents a "Visit Site" interstitial to unauthenticated browsers that breaks automated ingestion
flows (Medium's import, image embeds, external viewers).

**Today's incident (2026-05-30)**: hit ngrok bandwidth pain while relaying a 235 MB pptx for the
demo. This is the same 235 MB pptx incident referenced in `KNOWN_BUGS #10` and the Dashboard
`file-dropper` row, but it surfaces a different problem direction: those cover uploads TO leads;
this surfaces a problem with serving artifacts FROM the system to external viewers, Medium, and
similar consumers.

Since Claude Soma already runs Caddy with the user's own domain (`claude.mayankgupta.in`), the
system should leverage that for file relay instead: faster, no third-party bandwidth limits, no
ngrok interstitial, and the user is already paying for the domain and VPS bandwidth.

#### Design goal

A dedicated path (`claude.mayankgupta.in/files/`) that Caddy routes to a shared persistent
relay directory (`/var/lib/claude-soma/relay/`). Files placed there by a `soma-relay` helper are
instantly accessible at a stable HTTPS URL on the user's own domain.

#### Considerations

**1. Authentication**

The admin panel is auth-gated via the GitHub OAuth handle `techfreakworm`. Two viable mechanisms
for file relay:

*Option A — Caddy `forward_auth` to the existing Next.js API.* Each inbound file request is
forwarded to a Next.js `/api/auth/check` endpoint. If that endpoint returns 2xx (valid session
cookie), Caddy proxies the file. If it returns 4xx, Caddy returns 403. This reuses the existing
GitHub OAuth session cookie with no new credentials to manage. The session cookie is scoped to
`claude.mayankgupta.in`; because the relay path is on the same origin (see endpoint shape below),
the cookie is present on every request without any NextAuth.js cookie-domain changes.

*Option B — Caddy `basicauth`.* A separate htpasswd credential in the Caddyfile. Simpler
Caddyfile config, no dependency on the Next.js app being up. But introduces a second identity
concept (username/password) alongside the existing GitHub OAuth gate, adds a credential to
rotate and manage, and breaks single sign-on behavior (the user must re-authenticate for files
even though they are already logged into the dashboard).

**Recommendation: `forward_auth` to Next.js (Option A).** It reuses the existing identity
system. The only requirement is a thin `/api/auth/check` route in the Next.js app that reads the
session cookie and returns 200 or 401 — a few-line addition. Option B's separate credential
makes the system harder to reason about and introduces a second auth surface to leak or forget.

**The Medium-public-asset wrinkle.** If a published Medium article links to
`claude.mayankgupta.in/files/hero.png`, Medium's embed flow makes an unauthenticated GET.
An auth-gated response returns 401, Medium does not render the image. Resolution: a two-tier
namespace.

- `/files/<lead-name>/...` — private, auth-gated via `forward_auth`. For internal artifacts,
  admin review, and files delivered to leads.
- `/files/pub/<uuid>/...` — unauthenticated, no auth directive. The random UUID slug is the
  access credential (equivalent to a Dropbox share link). The `soma-relay` helper generates
  the UUID token at publish time and symlinks or copies the file into the pub sub-directory.
  The social-publish pipeline uses this path when it produces images or assets that need to
  be embedded in public posts. The public namespace is opt-in; the default for all files is
  private.

**2. HTTPS**

No action needed beyond what is already in place. Caddy's ACME (Let's Encrypt) integration is
live for `claude.mayankgupta.in`. The path-suffix approach (see below) means the relay path is
served under the existing cert; no new cert request or SAN expansion is required.

**3. TLS cert SAN expansion (subdomain trade-off)**

The endpoint shape choice determines the cert story.

*Subdomain route (`files.claude.mayankgupta.in`)*: a new DNS A record and a new Caddy site
block are required. Caddy auto-issues the cert on first HTTPS request once DNS propagates — a
one-time operational step. The subdomain has its own cookie scope, which is a problem here:
the session cookie set by the Next.js app is scoped to `claude.mayankgupta.in`, not to
`files.claude.mayankgupta.in`. The browser will not send it on requests to the subdomain, so
`forward_auth` sees no cookie and the check fails. Mitigation — set the cookie on the parent
domain `.mayankgupta.in` — requires a NextAuth.js config change that has broader scope and
audit implications.

*Path-suffix route (`claude.mayankgupta.in/files/`)*: one additional `handle /files/*` block
inside the existing Caddy site. No DNS change. No new cert. The existing session cookie is
present on every `/files/` request (same origin). `forward_auth` works without any NextAuth.js
changes.

**Recommendation: path-suffix.** The cookie-scope issue with the subdomain route makes
`forward_auth` structurally awkward without a cross-cutting NextAuth.js change. Path-suffix is
strictly simpler, requires no DNS changes or cert expansion, and produces clean URLs that are
consistent with the rest of the admin surface (`claude.mayankgupta.in/admin`,
`claude.mayankgupta.in/api`, `claude.mayankgupta.in/files`).

**4. Graceful fallback**

If `SOMA_RELAY_DOMAIN` is not set, the domain is misconfigured, DNS is not pointing to the VPS,
or the cert has lapsed, file relay must not fail silently. The fallback logic lives entirely in
the `soma-relay` helper, not in the application code. The application calls `soma-relay <file>`
and receives a URL; it does not know which backing mechanism was used.

Fallback decision tree in `soma-relay`:
1. If `SOMA_RELAY_DOMAIN` is set: optionally verify reachability (`SOMA_RELAY_HEALTH_CHECK=1`
   issues a HEAD request to `https://${SOMA_RELAY_DOMAIN}/files/health`; default off to avoid
   latency on every relay call). If reachable (or health check is off): use the Caddy path.
2. If `SOMA_RELAY_DOMAIN` is unset, or the health check fails: fall back to the existing
   `markserv + ngrok` pattern transparently. The caller receives an ngrok URL exactly as today.

This means the ngrok fallback is always available as a safety net. The Caddy path activates only
when explicitly configured.

**5. Migration**

The current `markserv + ngrok` pattern stays as the fallback path and requires no changes.
Migration is gated entirely on adding the env knob and the Caddyfile snippet.

Env knobs (added to `/etc/claude-soma/secrets.env`):

| Knob | Default | Meaning |
|---|---|---|
| `SOMA_RELAY_DOMAIN` | _(absent)_ | When set (e.g. `claude.mayankgupta.in`), enables the Caddy relay path. Absent = ngrok fallback. |
| `SOMA_RELAY_DIR` | `/var/lib/claude-soma/relay/` | Directory Caddy serves files from. Persistent (survives reboots). Created by the install wizard. |
| `SOMA_RELAY_HEALTH_CHECK` | `0` | Set to `1` to issue a HEAD check before selecting the Caddy path. Useful during initial setup; leave off in production. |
| `SOMA_RELAY_PUB_SUBPATH` | `pub` | Sub-path for unauthenticated public files (default: `/files/pub/<uuid>/...`). |

Migration steps:
1. Implement `scripts/soma-relay` (wraps both paths; default behavior without `SOMA_RELAY_DOMAIN`
   is identical to the current ngrok flow — zero regression on fresh installs or misconfigured
   setups).
2. Update `social-publish` and any other pipeline that calls ngrok directly to call
   `soma-relay` instead.
3. Operator adds `SOMA_RELAY_DOMAIN=claude.mayankgupta.in` to `secrets.env` and adds the
   `handle /files/*` + `handle /files/pub/*` blocks to the Caddyfile.
4. Install wizard (MULTI_PLATFORM_INSTALL Phase 1 or Phase 2): plant the Caddyfile snippet and
   create `SOMA_RELAY_DIR` automatically when the domain is configured.

**6. Per-lead isolation**

Files from different leads risk filename collisions (`output.pdf`, `diagram.png` are both common
default names). Cleanup after retiring a lead is also easier with a per-lead directory.

**Recommendation: yes.** Default directory structure: `/var/lib/claude-soma/relay/<lead-name>/`.
The lead name is already in the environment for every spawned lead (`HERMES_PROJECT_NAME` or the
tmux session name injected at spawn). `soma-relay` reads the lead name from env and places files
in the corresponding sub-directory. Public artifacts: `/var/lib/claude-soma/relay/pub/<uuid>/`
(the UUID token is the namespace; lead name is optional in the file metadata, not the path).

#### Recommended design

- **Auth**: Caddy `forward_auth` to `https://claude.mayankgupta.in/api/auth/check` (Next.js
  session cookie, GitHub OAuth gate). Private by default. Public opt-in via tokenized
  `/files/pub/<uuid>/...` sub-path (no auth directive; UUID slug is the credential).
- **Endpoint**: path-suffix `claude.mayankgupta.in/files/` (not a subdomain). Same origin as
  the dashboard; existing cert and session cookie cover it; no DNS change required.
- **HTTPS**: existing Caddy ACME cert covers the path. No action.
- **Fallback**: `soma-relay` helper selects the Caddy path when `SOMA_RELAY_DOMAIN` is set and
  reachable; falls back transparently to `markserv + ngrok` otherwise. Application code is
  decoupled from the backing mechanism.
- **Migration**: gated on `SOMA_RELAY_DOMAIN` env knob in `secrets.env` + one Caddyfile block.
  ngrok stays as fallback permanently; no forced cutover.
- **Per-lead isolation**: yes. Files land under `/var/lib/claude-soma/relay/<lead-name>/`;
  public files under `/var/lib/claude-soma/relay/pub/<uuid>/`.

#### Cross-references

- **`KNOWN_BUGS #10`** — same 235 MB pptx incident, opposite direction. Bug #10 covers the
  channel stall when the Telegram bot tried (and failed) to download the file (upload TO the
  system via Telegram). This section covers serving artifacts FROM the system to external
  consumers (Medium, demo viewers). Two different problems triggered by the same physical file.
- **Dashboard `Admin file dropper` row** — the upload-direction counterpart. The file-dropper
  handles getting large files INTO leads' inboxes (drag-drop on the admin page, multipart
  streaming, authenticated via the existing GitHub gate). The relay handles getting files OUT.
  Together they form a complete large-file workflow.
- **Dashboard `Hard-coded domain/handle cleanup` row** — `SOMA_RELAY_DOMAIN` must be the SAME
  config knob as the dashboard's domain setting. Both `wizard/init.py`
  (`render_caddyfile(domain, ...)`) and `api/main.py` (hard-coded `claude.mayankgupta.in` in the
  CORS origin list at line 13) need to converge on a single `SOMA_DOMAIN` or `SOMA_RELAY_DOMAIN`
  value from `secrets.env`. The relay naming can follow that cleanup rather than inventing a
  separate knob.
- **`MULTI_PLATFORM_INSTALL.md` Phase 1 / Phase 2** — the install wizard's
  `render_caddyfile()` step (`wizard/init.py:70`) is the natural place to plant the
  `/files/*` and `/files/pub/*` Caddyfile blocks when a public domain is configured. Phase 2
  (service-manager adapter + path abstraction) is also the right place to create
  `SOMA_RELAY_DIR` with correct ownership.

#### Out of scope

- **`markserv` removal** — stays as the fallback path; removing it is a separate decision once
  the Caddy relay is validated in production.
- **Caddyfile edits in this doc commit** — this section is a forward-looking design spec. Actual
  Caddyfile changes happen in the implementation commit (no Caddyfile edits here).
- **Public-namespace mechanism for Medium-linked assets** — flagged above (tokenized `pub`
  sub-path) as the recommended approach, but the exact implementation (UUID generation, symlink
  vs copy, TTL for public tokens) is a separate decision at implementation time.
- **`soma-relay` script creation** — no script shipped in this commit; the spec is here for the
  implementer.

## Packaging / Install

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| ~~Bootstrap iptables order fix~~ **DONE** (in `vps_bootstrap.sh` step 2/9; OCI-only gate added — pass `--cloud=oci`) | Bake the position-5 insertion (before Oracle's REJECT) into `vps_bootstrap.sh` and the wizard. Document in NEXT.md B2. | S | — |
| Multi-platform install | The big one: make Soma installable beyond a single OCI Ubuntu ARM box. Full plan in [`MULTI_PLATFORM_INSTALL.md`](MULTI_PLATFORM_INSTALL.md). | L | dedicated doc |
| `marketplace.json` publish test | Confirm `/plugin marketplace add techfreakworm/claude-soma` works end-to-end (V1.5 checklist item). | S | — |
| `.claude-plugin` author-object fix carry-forward | `author` had to be an object (Zod). Ensure forks don't regress. | S | — |
| Forking guide automation | README "Forking" section is a manual find-replace list; the install wizard could template these. | M | config layer |
| **`.mcp.json` env vs `secrets.env` single source of truth** | `HERMES_MAX_CONCURRENT_PROJECTS` (and similar tunables) defined in `.mcp.json`'s `env` block silently override `secrets.env` because the MCP server's process env is set before `secrets.env` is sourced. This cost a debugging cycle today when a bot edit to `secrets.env` had no effect. Recommended fix: drop the tunable from the `.mcp.json` env block entirely and rely on the server's `int(os.environ.get(..., "6"))` default for fresh installs; operators set overrides only in `secrets.env`. Option (b) — template `.mcp.json` from `secrets.env` — is heavier and less necessary. | S | — |

## Observability

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| **Routines registry population** | Wire `register_routine()` at three call sites: `schedule-routine` skill (cloud), bot-created local timers (`bot`), and the wizard's default timers (`system`). Until then `/api/routines` synthesizes `created_by` and never shows canonical `bot`/`user`. | M | — |
| Store systemd unit name in routine metadata | `metadata.unit` so the merger stops relying on heuristic `<name>`↔`claude-soma-<name>.timer` aliasing. Comes naturally with the population work. | S | routines population |
| ~~Logrotate~~ **DONE** (`scripts/logrotate-claude-soma` + bootstrap step) | Add a logrotate stanza for `/var/log/claude-soma/*.log` (per-lead + channel + api logs grow unbounded). | S | — |
| Cleaner lead transcript | Per-lead logs are raw PTY bytes (TUI escape sequences). A clean transcript needs claude-side support; interim = ship an ANSI-stripper helper. | M | — |
| Usage-snapshot validation | T11 in the checklist (first daily snapshot row) is still unverified. | S | — |

## Security

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| ~~Backup `secrets.env`~~ **DONE** (`scripts/backup-secrets.sh` + GPG symmetric encryption + daily timer) | Single point of failure: OAuth token + Telegram token + `AUTH_SECRET`. Encrypt + store off-box (checklist Item F). | S | — |
| Rotate leaked Telegram token | A bot token was pasted in a transcript (checklist Item G). `/revoke` via BotFather + update secrets. | S | — |
| `--dangerously-skip-permissions` blast radius | The bot runs with full tool access (no human to approve). Mitigated by the Telegram allowlist; revisit a scoped-permission mode if multi-user. | M | — |
| Playwright cookie store hardening | `~/.claude-pw/*` holds live session cookies (chmod 600/700 today). Consider encryption at rest. | M | — |

## Trading / V2

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| (Placeholder) Trading workstream | Referenced as a V2 theme in the brief. No code or spec exists in-repo today; this is a forward-looking bucket, not committed scope. Capture a spec before any build. | L | spec first |

## Grok Build integration (NEEDS REVIEW — clashes with codex-image-gen)

> **STATUS: PROPOSAL — needs user review + approval before any codification.**

### Surface info (live test, 2026-05-29)

- **Image generation**: `grok -p "/imagine <PROMPT>" --output-format json`
- **Video generation**: `grok -p "/imagine-video <PROMPT>" --output-format json`
- **Auth**: OIDC via `~/.grok/auth.json` (user is `techfreakworm@gmail.com`, tier 4 — no env API key needed)
- **Output location**: `/home/ubuntu/.grok/sessions/<url-encoded-cwd>/<session-id>/{images,videos}/<n>.{jpg,mp4}` — the path comes back in the JSON envelope `text` field as a markdown image/video link, regex-parseable
- **Timing**: image ~20s; video ~70s for a 5s 720p 16:9 clip at default params
- **Steerability**: duration / aspect / resolution adjustable via natural language in the prompt
- **Tier 4** covers both image AND video; no rate-limit messages observed during today's test

### Clash with `codex-image-gen`

Both target image generation via a CLI hand-off pattern. `codex-image-gen` routes to the user's ChatGPT (Codex CLI) subscription; the proposed `grok-build` routes to xAI (Grok). No code change is proposed here — this section documents the clash and the trade-offs so a decision can be made.

### Three viable paths (no recommendation — user decision needed)

| Path | Description | Pros | Cons | Effort |
|---|---|---|---|---|
| (a) Keep both, route by user preference | Both skills coexist; user picks per request | No regression; lets user A/B subjective output quality | Two skills to maintain; ambiguous default when user doesn't specify | S |
| (b) Replace `codex-image-gen` entirely with `grok-build` | Single image-gen path, Grok-only | One skill to maintain; tier 4 covers both image+video so video gets "free" | Loses Codex routing entirely; can't easily compare; if Grok output regresses there's no fallback | S |
| (c) Wrap both under a generic `image-gen` skill | Common interface, provider arg switches backends | Cleanest abstraction; future-proofs adding more providers; consistent skill API for the bot | Most work; adds an abstraction layer that may be over-engineering until a 3rd provider lands | M |

### Video (decoupled from the image-clash question)

Grok Build is the **only CLI video-gen option currently surveyed** — Codex has no video subcommand, no equivalent generic CLI surfaced. Video integration via Grok is therefore largely independent of the image-clash question above. Even if path (b) is rejected for image, a `grok-video` skill (or equivalent) can ship in isolation. Worth noting: video integration could ship first, ahead of any resolution of the image-clash.

### What needs user decision before any codification

- (a) vs (b) vs (c) for image integration
- Whether to ship video integration ahead of resolving the image-clash question
- Naming convention if path (c): `image-gen` with `--provider grok|codex` arg, or separate `image-gen-grok` / `image-gen-codex` siblings?

---

## Open questions

1. Is the subagent `--settings` inheritance question (KNOWN_BUGS #1 residual) ever going to be
   closed empirically, or do we commit to the "route heavy work to leads" fallback as the design?
2. Does the multi-platform effort target *operators* (self-host anywhere) or also *contributors*
   (run the test suite + a degraded local instance on macOS/Windows)? Scope differs a lot.
3. Trading/V2: real roadmap item or aspirational? Needs a spec before it earns priority.

## Recommended first steps

The three smallest high-value wins (all **DONE** as of the round-2 deploy):
1. ~~Wire `kill_session()` into `kill_project_impl` (Orchestration #1).~~ **DONE** (`238f78f`)
2. ~~Add the logrotate stanza (Observability).~~ **DONE** (`scripts/logrotate-claude-soma`)
3. ~~Back up + rotate `secrets.env` / Telegram token (Security).~~ **DONE** (`scripts/backup-secrets.sh` + daily timer)

Next: the **M** routines-population work, which unblocks accurate dashboard observability.
