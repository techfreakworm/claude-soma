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

---

## Priority summary (top of the backlog)

| # | Item | Theme | Effort | Why it's near the top |
|---|---|---|---|---|
| 1 | Wire `kill_session()` into `kill_project_impl` | Orchestration | S | Killing a project leaves the tmux/cgroup alive (registry-only `killed`). Correctness gap. |
| 2 | Bot-side `register_routine()` calls | Observability | M | Routines table exists but nothing populates it; `created_by` never canonical. |
| 3 | Fix Phase-1 bootstrap iptables ordering into `vps_bootstrap.sh`/wizard | Packaging | S | Known footgun: ACCEPT must precede Oracle's REJECT (pos 5, not 6). |
| 4 | Killed-lead resume (`--session-id`/`--resume`) | Orchestration | M/L | A dead lead loses all context today. See KNOWN_BUGS #2. |
| 5 | Demo video on landing page (replace placeholder) | Dashboard/Social | M | **IN PROGRESS** via `social-publish`. Last visible V1.5 ship-blocker. |
| 6 | Logrotate for `/var/log/claude-soma/*.log` | Observability | S | Per-lead + channel logs grow unbounded. |
| 7 | `NEEDS_REAUTH-<platform>` surfacing to the user | Social | S | Playwright auth silently rots; user only finds out when a post fails. |

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
| **Wire `kill_session()` into `kill_project_impl`** | Spawner has `kill_session()` (stops the unit + tmux) but `server.py::kill_project_impl` still only sets registry `status='killed'`. ~10-line change. **High priority.** | S | — |
| **Killed-lead resume** | Respawn a dead-but-not-retired lead with `--session-id`/`--resume` so it keeps history. Team teammates are the hard part (ephemeral; recommend v1 = lead re-dispatches). See KNOWN_BUGS #2. | M/L | liveness reconciliation (done) |
| Team-roster persistence | Persist teammate names+briefs at dispatch so a resumed lead can re-establish its team. | M | killed-lead resume |
| Exact teammate handles in graph | `discover_team()` is coarse (pane-derived `teammate-N`, no `@ping` handle). Have leads self-report into a registry table. | M | — |
| Reaper ↔ resume integration | `scripts/reaper.py` hibernates idle leads at 24 h / hard-deletes at 7 d; integrate with resume so hibernated leads can wake with state. | M | killed-lead resume |
| Concurrency-cap tuning | `HERMES_MAX_CONCURRENT_PROJECTS=6` is a static guess; make it memory-aware. | S | — |

## Dashboard

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| **Demo video** | Replace `frontend/components/landing/DemoVideo.tsx` `[demo video placeholder]` with the real 60-s build-log video. **IN PROGRESS** (social-publish campaign). | M | video asset |
| Routines cloud-cache prewarm | First request per TTL still pays ~12 s for the cloud `claude -p` query. Extend the cache-refresh timer to prewarm so no user waits. | S | — |
| Landing live-stats polish | Hero + live `/api/public/stats` exist; polish copy/screenshot for the showcase. | S | — |
| Per-lead log viewer in admin | Surface `/var/log/claude-soma/<name>.log` (ANSI-stripped) in the admin UI for crash forensics. | M | logrotate; ANSI strip |
| Hard-coded domain/handle cleanup | `claude.mayankgupta.in` vs `soma.mayankgupta.in` drift across `api/main.py`, `wizard/init.py`, Caddyfile, units. Centralize (ties into the install config layer). | S/M | MULTI_PLATFORM_INSTALL config layer |

## Social

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| `NEEDS_REAUTH` surfacing | `pw-refresh.js` drops a `~/.claude-pw/NEEDS_REAUTH-<platform>` sentinel when a session dies; today only the journal shows it. Have the healthcheck or a bot routine DM the user. | S | — |
| Routines-cache prewarm for posts | (shared with dashboard) keep playwright sessions warm so a scheduled post never hits a cold login wall. | S | — |
| More platforms | Writer/poster agents exist for X (thread + Article), LinkedIn, Medium. Candidates: Bluesky, Mastodon, Threads. | M each | shared-auth pattern |

## Packaging / Install

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| **Bootstrap iptables order fix** | Bake the position-5 insertion (before Oracle's REJECT) into `vps_bootstrap.sh` (done in script — verify) and the wizard. Document in NEXT.md B2. | S | — |
| Multi-platform install | The big one: make Soma installable beyond a single OCI Ubuntu ARM box. Full plan in [`MULTI_PLATFORM_INSTALL.md`](MULTI_PLATFORM_INSTALL.md). | L | dedicated doc |
| `marketplace.json` publish test | Confirm `/plugin marketplace add techfreakworm/claude-soma` works end-to-end (V1.5 checklist item). | S | — |
| `.claude-plugin` author-object fix carry-forward | `author` had to be an object (Zod). Ensure forks don't regress. | S | — |
| Forking guide automation | README "Forking" section is a manual find-replace list; the install wizard could template these. | M | config layer |

## Observability

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| **Routines registry population** | Wire `register_routine()` at three call sites: `schedule-routine` skill (cloud), bot-created local timers (`bot`), and the wizard's default timers (`system`). Until then `/api/routines` synthesizes `created_by` and never shows canonical `bot`/`user`. | M | — |
| Store systemd unit name in routine metadata | `metadata.unit` so the merger stops relying on heuristic `<name>`↔`claude-soma-<name>.timer` aliasing. Comes naturally with the population work. | S | routines population |
| **Logrotate** | Add a logrotate stanza for `/var/log/claude-soma/*.log` (per-lead + channel + api logs grow unbounded). | S | — |
| Cleaner lead transcript | Per-lead logs are raw PTY bytes (TUI escape sequences). A clean transcript needs claude-side support; interim = ship an ANSI-stripper helper. | M | — |
| Usage-snapshot validation | T11 in the checklist (first daily snapshot row) is still unverified. | S | — |

## Security

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| **Backup `secrets.env`** | Single point of failure: OAuth token + Telegram token + `AUTH_SECRET`. Encrypt + store off-box (checklist Item F). | S | — |
| Rotate leaked Telegram token | A bot token was pasted in a transcript (checklist Item G). `/revoke` via BotFather + update secrets. | S | — |
| `--dangerously-skip-permissions` blast radius | The bot runs with full tool access (no human to approve). Mitigated by the Telegram allowlist; revisit a scoped-permission mode if multi-user. | M | — |
| Playwright cookie store hardening | `~/.claude-pw/*` holds live session cookies (chmod 600/700 today). Consider encryption at rest. | M | — |

## Trading / V2

| Item | What / Why | Effort | Depends on |
|---|---|---|---|
| (Placeholder) Trading workstream | Referenced as a V2 theme in the brief. No code or spec exists in-repo today; this is a forward-looking bucket, not committed scope. Capture a spec before any build. | L | spec first |

---

## Open questions

1. Is the subagent `--settings` inheritance question (KNOWN_BUGS #1 residual) ever going to be
   closed empirically, or do we commit to the "route heavy work to leads" fallback as the design?
2. Does the multi-platform effort target *operators* (self-host anywhere) or also *contributors*
   (run the test suite + a degraded local instance on macOS/Windows)? Scope differs a lot.
3. Trading/V2: real roadmap item or aspirational? Needs a spec before it earns priority.

## Recommended first steps

The three smallest high-value wins, all **S**, no new dependencies:
1. Wire `kill_session()` into `kill_project_impl` (Orchestration #1).
2. Add the logrotate stanza (Observability).
3. Back up + rotate `secrets.env` / Telegram token (Security).

Then the **M** routines-population work, which unblocks accurate dashboard observability.
