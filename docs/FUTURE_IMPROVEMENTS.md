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

### Surfaced 2026-05-29 (from live T1–T5 acceptance + gate false-positive)

Not yet prioritized above the existing top-7; tracked here for visibility. Both are **S**.

- **SCHEDULE-ROUTINE bash-fallback** — the working one-shot Telegram reminder path (`nohup setsid bash` + Bot-API `sendMessage`) is tribal knowledge; codifying it in the skill prevents reinvention. `RemoteTrigger` v2 is stale per memory (HTTP 400). Validated T4, 2026-05-29. See Orchestration section.
- **Orchestrator gate false positives** — `9b26c72` gate flags heredoc bodies that *mention* network tools (not invoke them) and `/tmp` writes. A few hours of heuristic tightening eliminates daily false-positive friction. Validated by two live bot denials, 2026-05-29. See Orchestration section.

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
