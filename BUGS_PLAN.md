# BUGS PLAN — sequenced fix roadmap

Generated: 2026-05-30 by soma-improver after a planning + prioritization pass.

---

## TL;DR

- **Round N** (start here): one maintenance-window bundle (token rotation + subagent vector verify + T1-T5
  close-out) plus five no-restart quick wins — gate false positives, `last_activity` bump, `.mcp.json`
  env fix, schedule-routine bash-fallback, and the concurrency-cap defensive guard.
- **Round N+1**: the two highest-leverage M items — lead notify channel (biggest daily UX gap) and the
  channel-stall root fix — plus the admin file dropper that completes the large-file upload story.
- **Round N+2**: architecture prerequisites — domain config unification (gates Caddy relay), Caddy file
  relay itself, and killed-lead resume (the long-tail L item that unblocks team-roster and reaper
  integration).
- Grok Build integration stays blocked until the user picks a path for the image-gen clash.
- Anything downstream of killed-lead resume (team-roster persistence, reaper integration) cannot move
  until resume lands; they are sequenced into Round N+3 or beyond.

---

## The shape of the queue (snapshot)

As of 2026-05-30 there are six open KNOWN_BUGS entries: two are verify-only tasks that close in the
next maintenance window (#7 subagent vector, #9 T1-T5 acceptance), two need implementation (#2
killed-lead resume, #10 channel stall), one has a code fix on main that needs empirical confirmation
(#1 poller hijack structural fix, same window as #7), and one is a dashboard observability gap (#4
routines registry). On the improvement side there are 21 ready-to-implement items ranging from trivial
S fixes (`.mcp.json` env override, `last_activity` column, token rotation) to full M features (lead
notify channel, Caddy file relay) to one architectural L item (killed-lead resume). One proposal —
Grok Build integration — is blocked on a user decision about the image-gen clash with `codex-image-gen`
and is not sequenced until that decision is made. The leaked Telegram token in the Security section
scores P0 (50 by the formula); it is the highest-priority single item in the queue and anchors the
maintenance window.

---

## Inventory + scores

`priority_score = severity_weight × leverage / effort_weight`
Weights: P0=10, P1=5, P2=2, P3=1. Effort: S=1, M=3, L=8. Leverage 1–5.

| ID | Title | Severity | Leverage | Effort | Risk | Score | Status |
|---|---|---|---|---|---|---|---|
| SEC-1 | Rotate leaked Telegram token | P0 | 5 | S | high | **50** | open |
| FI-GATE | Orchestrator gate false positives | P1 | 4 | S | low | **20** | open |
| BUG-7 | Subagent vector verify (poller hijack residual) | P1 | 4 | S | high | **20** | verify-only (maintenance window) |
| FI-NOTIFY | Lead → orchestrator notify channel | P1 | 5 | M | medium | **8.33** | open |
| FI-ACT | `last_activity` column not bumped by tmux send-keys | P2 | 4 | S | low | **8** | open |
| BUG-9 | T1–T5 acceptance verify-close | P2 | 3 | S | low | **6** | verify-only (today's live run) |
| FI-MCP | `.mcp.json` env vs `secrets.env` source of truth | P2 | 3 | S | low | **6** | open |
| BUG-10 | Channel stall on large attachment | P1 | 3 | M | medium | **5** | open |
| FI-SCHED | Codify schedule-routine bash-fallback | P2 | 2 | S | low | **4** | open |
| FI-CAP | Concurrency-cap accounting (defensive) | P2 | 2 | S | low | **4** | open |
| FI-PREWARM | Routines cloud-cache prewarm | P2 | 2 | S | low | **4** | open |
| FI-DROPPER | Admin file dropper for large uploads (>20 MB) | P1 | 2 | M | medium | **3.33** | open |
| FI-CADDY | Caddy-via-own-domain file relay (replace ngrok) | P2 | 3 | M | medium | **2** | open |
| FI-ARCHIVE | `kill_project` archive log for no-memory leads | P3 | 2 | S | low | **2** | open |
| FI-DOMAIN | Hard-coded domain/handle cleanup | P2 | 3 | M | medium | **2** | open |
| FI-T11 | Usage-snapshot validation (T11) | P2 | 1 | S | low | **2** | verify-only |
| BUG-4 | Routines registry never populated | P2 | 2 | M | medium | **1.33** | open |
| FI-TEAM | Team-roster persistence | P2 | 2 | M | medium | **1.33** | blocked: depends on BUG-2 |
| FI-REAPER | Reaper ↔ resume integration | P2 | 2 | M | medium | **1.33** | blocked: depends on BUG-2 |
| FI-PW | Playwright cookie store hardening | P2 | 2 | M | medium | **1.33** | open |
| BUG-2 | Killed-lead resume (`--session-id`/`--resume`) | P1 | 2 | L | high | **1.25** | open |
| FI-MKT | `marketplace.json` publish test | P3 | 1 | S | low | **1** | open |
| FI-LOG | Per-lead log viewer in admin | P3 | 2 | M | low | **0.67** | open |
| FI-PLAT | More social platforms (Bluesky, Mastodon, Threads) | P3 | 2 | M | low | **0.67** | open |
| FI-GRAPH | Exact teammate handles in graph | P3 | 2 | M | low | **0.67** | open |
| FI-FORK | Forking guide automation | P3 | 1 | M | low | **0.33** | open |
| GROK | Grok Build integration (image + video) | — | — | S/M | medium | — | blocked: user decision |

*Dependency-demoted rows: FI-TEAM and FI-REAPER score 1.33 but cannot start until BUG-2 lands.
FI-CADDY scores 2 but cannot start until FI-DOMAIN converges the config layer (equal score, topology tie-break).*

---

## Sequenced roadmap

### Round N (next round — start here)

#### Maintenance-window bundle (one channel restart covers all three)

- **SEC-1: Rotate leaked Telegram token** (P0, leverage 5, effort S, restart required)
  - **Why now**: A bot token was pasted in a transcript and has not been revoked. This is the single
    highest-scoring item in the entire queue (50 by formula: P0 × leverage 5 / S). A leaked token is
    an always-open door regardless of everything else in the queue. The channel restart it requires is
    a cost already being paid for items below — bundle it first so nothing runs on a compromised token.
  - **Depends on**: none (BotFather `/revoke` + `secrets.env` update + `systemctl restart`)
  - **Out-of-band considerations**: restart is the gate event for the two verify-only items below;
    schedule all three in the same window.

- **BUG-7: Subagent vector verify** (P1, leverage 4, effort S, restart required)
  - **Why now**: The poller-hijack structural fix (`4082e3b`) is on main and deployed per repo memory
    (2026-05-26). The residual is empirical: dispatch one trivial background Agent after the window
    restart, watch ~35 s for a second `bun server.ts` process or a changed `bot.pid`. If a second bun
    appears, the fallback is already designed — route heavy work to orchestrator-spawned leads (already
    plugin-skipped + cgroup-isolated) via `system_prompts/responsive_bot.md`. The 35-second window from
    the earlier partial test is the partial-close; this is the channel-side confirmation that fully
    closes #1/#7.
  - **Depends on**: maintenance window (channel restart); token rotation (SEC-1) should fire first so
    the test runs on a fresh, known-good token.
  - **Out-of-band considerations**: if inheritance is confirmed, the fallback decision (route via leads)
    needs a system prompt edit before the window closes — have the edit staged in advance.

- **BUG-9: T1–T5 acceptance verify-close** (P2, leverage 3, effort S, low risk)
  - **Why now**: The checklist records T1–T5 as Pending despite the spawner rewrite landing in
    `d31df9a` months ago. Today's live T1–T5 run should have exercised spawn / status / kill / message /
    schedule against the deployed bot. If the run returned green, update `docs/CHECKLIST.md` and close
    #9. If any leg failed, promote the failure to a new Round N item immediately (before the window
    closes). Note that T3 (kill) additionally exercises the `kill_project_impl` post-kill liveness
    verification hardened in `c99c743`; a clean T3 confirms that fix in the field.
  - **Depends on**: maintenance window (the restart reloads `kill_project_impl`'s hardened code)
  - **Out-of-band considerations**: if T3 produces a zombie lead, `is_lead_alive()` retry path fires
    and raises `RuntimeError` — that surface is good evidence the hardening works.

#### No-restart sweep (can ship any time, no restart required)

- **FI-GATE: Orchestrator gate false positives** (P1, leverage 4, effort S, restart NOT required)
  - **Why now**: `9b26c72` shipped the gate on 2026-05-29, and within the same day two live false
    positives hit: (a) a Bash heredoc whose body *describes* network tools was flagged as a network
    shell-out; (b) a `Write` call to `/tmp` was denied. The gate fires on every bot message, so two
    daily false positives = compounding friction. The fix is a few hours of heuristic tightening in
    `scripts/orchestrator_gate.sh`: match on the first non-pipeline token of the Bash command (the
    actual invocation, not `grep -qE` over the full command string), and scope the Write deny to
    production paths (`/opt/claude-soma/`) rather than any Write call. The gate script is exec'd fresh
    per PreToolUse event — no restart required. Score 20 ties with BUG-7 but executes immediately.
  - **Depends on**: `9b26c72` (done)
  - **Out-of-band considerations**: after the fix, monitor `~/.claude-soma/activity.jsonl` for one
    day to confirm the false-positive rate drops to zero.

- **FI-ACT: `last_activity` column not bumped by tmux send-keys** (P2, leverage 4, effort S)
  - **Why now**: Validated on 2026-05-29 during T3 acceptance — `t1-spawn-test`'s `last_activity`
    stayed at spawn timestamp despite an actively delivered and answered message routed through
    `tmux send-keys`. The reaper (`53f7113`) now correctly skips hibernation when the tmux session
    is alive, but the stale column still produces misleading "idle for Xh" labels in the admin graph
    and corrupts the daily-status digest. One-line hook in the send-keys code path; S effort.
  - **Depends on**: none
  - **Out-of-band considerations**: confirm the admin "idle" badge updates after the next `tmux send-keys`
    message to any active lead.

- **FI-MCP: `.mcp.json` env vs `secrets.env` source of truth** (P2, leverage 3, effort S)
  - **Why now**: This cost a debugging cycle today — a bot edit to `secrets.env` for
    `HERMES_MAX_CONCURRENT_PROJECTS` had no effect because the MCP server's process env was seeded
    from `.mcp.json`'s `env` block before `secrets.env` was sourced. The fix is to drop the tunable
    from `.mcp.json`'s env block and rely on the server's `int(os.environ.get(..., "6"))` default;
    operators set overrides only in `secrets.env`. Trivial removal; prevents future silent override
    surprises.
  - **Depends on**: none
  - **Out-of-band considerations**: verify with `systemctl show claude-soma-channel.service
    | grep HERMES_MAX_CONCURRENT` after the next restart to confirm the env origin.

- **FI-SCHED: Codify schedule-routine bash-fallback** (P2, leverage 2, effort S)
  - **Why now**: Validated today (T4 acceptance, 2026-05-29: fired on schedule, exit 0). The working
    one-shot Telegram reminder path — `nohup setsid bash` + `sleep` + sourcing the bot token from
    `~/.claude/channels/telegram/.env` + a Bot-API `sendMessage` — is tribal knowledge that will be
    reinvented every session. `RemoteTrigger` v2 is stale (HTTP 400 on the `{name, cron, prompt}` body
    shape per repo memory). Wrap the working pattern into the `schedule-routine` skill and/or
    `hermes-api` MCP so it is discoverable.
  - **Depends on**: none
  - **Out-of-band considerations**: none; no restart.

- **FI-CAP: Concurrency-cap accounting (defensive)** (P2, leverage 2, effort S)
  - **Why now**: With `c99c743` and `53f7113` landed, stale-registry drift is rare but not impossible
    (a crash between the kill and the registry update could leave a dead lead counted as active, blocking
    new spawns). Belt-and-suspenders: intersect the active-row count with actual `is_lead_alive()`
    before checking against the cap. S effort; same commit as FI-ARCHIVE since they are both
    one-liner follow-ups to recent spawner changes.
  - **Depends on**: `c99c743`, `53f7113` (done)
  - **Out-of-band considerations**: none.

- **FI-ARCHIVE: `kill_project` archive log for no-memory leads** (P3, leverage 2, effort S)
  - **Why now**: Trivial (one log line); bundle it in the same commit as FI-CAP to avoid wasting a
    commit slot. A no-op archive path that silently returns `None` makes caller code unable to
    distinguish "archived" from "skipped" — which matters when debugging test leads.
  - **Depends on**: none
  - **Out-of-band considerations**: none.

- **FI-PREWARM + FI-T11** (P2/P2, leverage 2/1, effort S each)
  - **FI-PREWARM**: The first `/api/routines` request per TTL still pays ~12 s for the cloud `claude -p`
    query despite the caching added in `9a76a75`. Extend the cache-refresh timer to prewarm so no user
    request waits for a cold cache. Pairs naturally with the routines population work in Round N+1.
  - **FI-T11**: Usage-snapshot T11 in the checklist is still marked unverified. Verify and mark done.
  - Both are S; neither requires a restart; bundle into the no-restart sweep.

---

### Round N+1 (after Round N lands)

- **FI-NOTIFY: Lead → orchestrator notify channel** (P1, leverage 5, effort M, restart NOT required
  for the new feature — new MCP server added to `lead-mcp.json`)
  - **Why now**: The single biggest UX friction in the current system. Every time a lead is working, the
    user must manually ping the bot with "Status?" to learn what the lead has done. The bot must
    `capture-pane` every active lead on every ping. This friction was observed roughly eight times on
    2026-05-30 alone. Lead notify channel's score of 8.33 is the highest of all M items in the queue —
    leverage 5 (every lead interaction, every completion event) combined with P1 severity. The
    recommended mechanism (localhost HTTP API + SQLite `lead_events` spool in `registry.sqlite`) is
    fully designed in `FUTURE_IMPROVEMENTS.md`. A `hermes-notify` MCP shim added to `lead-mcp.json`
    is the narrowest possible scope expansion for leads — one tool (`notify_orchestrator`), no spawn
    or kill access.
  - **Depends on**: none
  - **Out-of-band considerations**: adding `hermes-notify` to `lead-mcp.json` takes effect on the next
    lead spawn (already-running leads do not get it until they are restarted or replaced). Consider
    also a one-line `HERMES_NOTIFY_PORT` env injection in the spawner at the same time.

- **BUG-10: Channel stall on large attachment** (P1, leverage 3, effort M, medium risk)
  - **Why now**: At 21:38–21:40 UTC on 2026-05-29, a 235 MB pptx upload triggered HTTP 400 "file is
    too big" from Telegram's `getFile` endpoint (~20 MB cap). The user's follow-up text messages
    during that window were marked "Request interrupted by user" — the channel went effectively deaf.
    The short-circuit fix (check `file_size` in the message metadata before calling `getFile`; surface
    a clear error if >20 MB cap) is the highest-ROI first step. The secondary fix — decoupling the
    download path from the inbound poller — is architecturally cleaner but requires deeper changes to
    the plugin. Investigate the channel log around 21:38–21:40 UTC first to confirm which hypothesis
    (a–d) is responsible, then apply the minimum fix.
  - **Depends on**: none (investigation-first)
  - **Out-of-band considerations**: channel restart may be needed to validate the fix; bundle with
    BUG-4 if both land in the same round.

- **FI-DROPPER: Admin file dropper for large uploads** (P1, leverage 2, effort M, medium risk)
  - **Why now**: The same 235 MB pptx incident that triggered BUG-10 forced a manual `scp` + hand-copy
    into the lead inbox. The dropper — a drag-drop zone on the per-lead admin page with multipart
    streaming and a manifest — addresses the upload direction while BUG-10 addresses the stall. Neither
    is complete without the other: stopping the stall without the dropper still leaves the user with no
    clean large-file upload path; shipping the dropper without the stall fix leaves the channel
    vulnerable if someone tries Telegram upload anyway. Land them together in Round N+1. The dropper is
    auth-gated via the existing GitHub OAuth gate and writes to a watched inbox per lead.
  - **Depends on**: BUG-10 (root fix first, or land in the same round)
  - **Out-of-band considerations**: multipart streaming requires testing with a real 200+ MB file on
    the VPS to confirm the API does not OOM; the manifest format should be locked in advance.

- **BUG-4 / FI-ROUTINES: Routines registry never populated** (P2, leverage 2, effort M)
  - **Why now**: `/api/routines` renders (via synthesis) but `created_by` is never canonical — always
    `system`/`cron`/`cloud`, never `bot` or `user`. The fix requires wiring `register_routine()` at
    three call sites: the `schedule-routine` skill (after creating a cloud RemoteTrigger), bot-created
    local timers, and the wizard's default timers. The prewarm fix (FI-PREWARM) done in Round N makes
    this a clean target for Round N+1 — the query layer will already be fast when the population
    code adds more rows.
  - **Depends on**: FI-PREWARM (routes cache; population makes rows visible faster)
  - **Out-of-band considerations**: store `metadata.unit` at the same time so the merger stops relying
    on heuristic name aliasing.

- **FI-MKT: `marketplace.json` publish test** (P3, leverage 1, effort S)
  - **Why now**: V1.5 checklist item with no dependencies; bundle it as a tack-on in Round N+1 since
    it is S effort and requires no restart. Confirming `/plugin marketplace add techfreakworm/claude-soma`
    works end-to-end closes the last packaging checklist item.
  - **Depends on**: none
  - **Out-of-band considerations**: none.

---

### Round N+2 (further out)

- **FI-DOMAIN: Hard-coded domain/handle cleanup** (P2, leverage 3, effort M, medium risk)
  - **Why now**: `claude.mayankgupta.in` vs `soma.mayankgupta.in` drift appears in `api/main.py`
    (CORS origin list, hard-coded at line 13), `wizard/init.py`, Caddyfile, and systemd units.
    This is the PREREQUISITE for Caddy file relay (FI-CADDY) — the relay design explicitly requires
    `SOMA_RELAY_DOMAIN` to converge with the dashboard domain config on a single `SOMA_DOMAIN` knob
    in `secrets.env`. Doing domain cleanup first prevents a third definition of the domain string
    being introduced by the Caddy relay implementation.
  - **Depends on**: MULTI_PLATFORM_INSTALL config layer (Phase 1 landed in `8e8c094`; Phase 2 TBD)
  - **Out-of-band considerations**: centralize in `secrets.env`; update `wizard/init.py`
    `render_caddyfile()` to read from it.

- **FI-CADDY: Caddy-via-own-domain file relay** (P2, leverage 3, effort M, medium risk)
  - **Why now**: ngrok bandwidth pain hit while relaying the 235 MB pptx on 2026-05-30; the ngrok
    interstitial also breaks automated Medium image ingestion. Since Caddy is already live on
    `claude.mayankgupta.in` with an ACME cert, adding a path-suffix `handle /files/*` block is
    trivially safe. The full design (two-tier namespace: private `/files/<lead>/` via `forward_auth`,
    public `/files/pub/<uuid>/` unauthenticated with UUID-as-credential; `soma-relay` helper with
    ngrok fallback; per-lead isolation) is already specified in `FUTURE_IMPROVEMENTS.md`. Cannot start
    until FI-DOMAIN unifies the `SOMA_DOMAIN` / `SOMA_RELAY_DOMAIN` knob.
  - **Depends on**: FI-DOMAIN
  - **Out-of-band considerations**: `soma-relay` helper must be written first; the Caddyfile change
    is one block.

- **BUG-2: Killed-lead resume** (P1, leverage 2, effort L, high risk)
  - **Why now**: A dead (non-retired) lead loses all conversation history, in-progress task state, and
    its team. The cgroup-isolation fix (`ae7d7be`) means channel restarts no longer kill leads, so
    the failure mode is rarer — but OOM, crashes, and explicit kills still happen. Resume requires
    (a) generating a UUID and passing `--session-id <uuid>` at first spawn, (b) persisting
    `name → uuid` in the registry, (c) on respawn building argv with `--resume <uuid>`. The team
    problem: recommend v1 = teams are ephemeral; the resumed lead re-plans from its own transcript.
    This is L effort and high-risk (cross-cutting spawner + registry changes). Scheduling it in
    Round N+2 gives it the runway it needs; it does not compete with the quick wins in Round N.
  - **Depends on**: liveness reconciliation (`a974011`, done)
  - **Out-of-band considerations**: the team-roster persistence and reaper-resume integrations
    (FI-TEAM, FI-REAPER) block on this landing; they cannot start until BUG-2 is shipped and stable.

- **FI-PW: Playwright cookie store hardening** (P2, leverage 2, effort M)
  - **Why now**: `~/.claude-pw/*` holds live session cookies (chmod 600/700 today). Encryption at rest
    is the remaining gap after the pw-refresh and NEEDS_REAUTH improvements. Low urgency but fits Round
    N+2 alongside the security-adjacent domain cleanup work.
  - **Depends on**: none (independent)
  - **Out-of-band considerations**: none.

- **FI-LOG: Per-lead log viewer in admin** (P3, leverage 2, effort M)
  - **Why now**: Logrotate landed in `5a001b3`. The next step is surfacing the ANSI-stripped logs in
    the admin UI for crash forensics. P3 polish; fits Round N+2 as a dashboard quality-of-life item.
  - **Depends on**: logrotate (done); ANSI stripper helper
  - **Out-of-band considerations**: decide on ANSI stripping approach (pipe through `col -b` or Python
    `strip-ansi` library) before implementing the viewer.

- **FI-PLAT: More social platforms** (P3, leverage 2, effort M each)
  - **Why now**: Writer/poster agents exist for X thread, X Article, LinkedIn, Medium. Bluesky,
    Mastodon, and Threads are candidates. P3 with no blocking dependencies; Round N+2 gives the
    social pipeline time to stabilize on the Caddy file relay before adding new surface area.
  - **Depends on**: FI-CADDY (stable relay for asset serving to new platforms)
  - **Out-of-band considerations**: each platform is independent M effort; sequence by user priority.

---

### Round N+3 (depends on BUG-2 landing)

The following items cannot start until killed-lead resume (BUG-2) is shipped and stable:

- **FI-TEAM: Team-roster persistence** (score 1.33 raw, demoted by dependency topology)
  - Persist teammate names + briefs at dispatch time so a resumed lead can re-establish its team.
  - **Depends on**: BUG-2

- **FI-REAPER: Reaper ↔ resume integration** (score 1.33 raw, demoted by dependency topology)
  - Integrate `scripts/reaper.py` hibernation with resume so hibernated leads can wake with state.
  - **Depends on**: BUG-2

- **FI-GRAPH: Exact teammate handles in graph** (P3, score 0.67)
  - `discover_team()` is pane-derived (`teammate-N`, no `@ping` handle). Have leads self-report into
    a registry table.
  - **Depends on**: none (independent, but low priority; fits naturally alongside team-roster work)

- **FI-FORK: Forking guide automation** (P3, score 0.33)
  - **Depends on**: MULTI_PLATFORM_INSTALL config layer (Phase 2)

---

### Blocked / awaiting user decision

- **GROK: Grok Build integration (image + video)**
  - Surface: `grok -p "/imagine"` (image, ~20 s) and `grok -p "/imagine-video"` (video, ~70 s for 5 s
    clip); OIDC auth via `~/.grok/auth.json`; live-tested 2026-05-29.
  - Clash: both `grok-build` and `codex-image-gen` target image generation via CLI hand-off. Three
    paths: (a) keep both, route by user preference; (b) replace `codex-image-gen` with `grok-build`;
    (c) generic `image-gen` skill with `--provider grok|codex` arg.
  - Video integration (no existing competitor) can ship independently regardless of the image decision.
  - **Blocked on user decision**: (a)/(b)/(c) for image; whether to ship video before the image choice
    is resolved; naming convention if path (c).
  - Do NOT sequence until the user picks.

---

## Why this order — the story

**Round N is shaped by two constraints that dominate everything else.** The first is the leaked
Telegram token — a P0 security item scoring 50 by the formula (the highest in the queue). It demands
a channel restart, and a channel restart is an operationally costly event. The second constraint is
that two verify-only items (#7 subagent vector, #9 T1-T5) also need exactly one channel restart to
close. The right move is obvious: pay the restart cost once and close all three. Everything else in
Round N is either a no-restart fix that can ship immediately (gate false positives, `last_activity`,
`.mcp.json` env) or a trivial S item that bundles into a single commit (concurrency-cap, archive log).
Gate false positives ties #7 on raw score (both 20) but fires immediately — the script is exec'd
fresh per PreToolUse event, so patching it is same-day relief for the daily friction the gate has
been causing since 2026-05-29.

**Round N+1 is the lead notify channel, and here leverage outvoted severity.** The channel stall
(BUG-10, score 5) has higher severity than the `last_activity` bump (score 8) but lower leverage;
it goes into Round N+1, not Round N, because it is M effort. The lead notify channel (score 8.33)
leads Round N+1 because leverage 5 — every lead interaction, every completion notification — is the
rarest combination in the queue: only one item scores both P1 and leverage 5. The user observed
this friction eight times on 2026-05-30 alone; at that rate the cumulative cost of the polling
overhead exceeds any single M-effort fix. BUG-10 and the file dropper (FI-DROPPER) land in Round N+1
together because they address opposite directions of the same 235 MB pptx incident: the stall fix
prevents the channel going deaf on failed downloads; the dropper gives the user a clean upload path
that bypasses Telegram's 20 MB cap. Neither is complete without the other.

**Round N+2 is governed by two dependency chains.** Domain cleanup (FI-DOMAIN) must precede Caddy
file relay (FI-CADDY) because both designs converge on a single `SOMA_DOMAIN` / `SOMA_RELAY_DOMAIN`
knob in `secrets.env` — shipping the relay before the cleanup would introduce a third definition of
the domain string and create exactly the kind of drift the cleanup is supposed to fix. The second
chain is killed-lead resume (BUG-2): its raw score (1.25) underrepresents its structural importance
because L effort drives the denominator up. But two items scoring 1.33 (team-roster persistence,
reaper-resume) cannot start without it. Those items are in the queue; resume must come first.

**Dependency topology forced three demotions from raw score.** Team-roster persistence (1.33) and
reaper-resume integration (1.33) both score above killed-lead resume (1.25), but both are structurally
blocked by it — they have no implementation path until `--session-id` + `--resume` exists in the
spawner. Caddy file relay (score 2) ties domain cleanup (score 2) on raw score but cannot start until
domain cleanup unifies the config knob; the topology makes the order unambiguous. These are the only
three demotions in the queue; every other sequencing decision follows directly from the score ranking.

**The Grok proposal sits outside the sequence by design.** It is genuinely blocked — not low-priority
but undecided. The image-gen clash (`codex-image-gen` vs `grok-build`) is a user preference call,
not a technical one. Video integration is cleanly separable and could ship before the image decision
is made. Until the user picks a path, nothing in the Grok section belongs in any round.

---

## Open questions for the user

1. **Leaked Telegram token urgency**: Is the token rotation P0-urgent enough to do today (outside the
   planned maintenance window), or can it wait for the next scheduled window? If the token was pasted
   in a public transcript accessible to third parties, immediate revocation is the right call.

2. **#7 subagent vector — fallback decision**: If the maintenance-window test reveals that subagents
   DO inherit `--settings` (and thus re-introduce the poller hijack), the fallback is to route all
   heavy work through orchestrator-spawned leads (already plugin-skipped + cgroup-isolated) via
   `system_prompts/responsive_bot.md`. Are you committed to that fallback as the design, or do you
   want to explore patching the third-party plugin (last-resort option; fragile on upgrade)?

3. **Grok Build integration**: (a) keep both `codex-image-gen` + `grok-build` and route by preference;
   (b) replace `codex-image-gen` entirely; (c) generic `image-gen` skill with `--provider` arg. Also:
   should video integration ship before the image-clash decision is resolved, since video has no
   existing competitor in the skill set?

4. **Killed-lead resume v1 scope**: For v1, the recommendation is option (a) — teams are ephemeral;
   the resumed lead re-plans from its own transcript. Is that acceptable, or does the full team
   restore (option b: persist the team roster and re-spawn teammates on resume) need to be in scope
   for v1?

5. **Caddy relay public-namespace token TTL**: The design specifies a tokenized `/files/pub/<uuid>/`
   path with no TTL (UUID slug as permanent credential). Should public tokens expire (e.g., 30 days),
   and if so, should expired tokens return 404 or redirect to a "link expired" page?

---

## Recently shipped (context — do not re-plan)

These commits closed items that are intentionally absent from the queue above:

- **`c99c743`** — `kill_project_impl` post-kill liveness verification + one retry; raises `RuntimeError`
  if the lead survives both attempts (prevents zombie rows masking as `killed`).
- **`53f7113`** — reaper skips hibernation when the tmux session is still alive (`is_lead_alive()`
  guard against stale `last_activity`).
- **`9b26c72`** — orchestrator hard gates: LOW default effort + PreToolUse deny-list hook
  (`scripts/orchestrator_gate.sh`).
- **`99be0fd`** — daily RC-URL refresh for project leads (captures fresh `/remote-control` URLs).
- **`7387bd0`** — `send_tg_reply` tool in `hermes_api`: GFM-to-HTML conversion for Telegram replies,
  replacing the plugin's `format='text'` default.
- **`5a001b3`** — round-2 small-batch: whisper `base.en`, Node 22, OCI iptables gate, logrotate,
  secrets backup, `NEEDS_REAUTH` ping surfacing. Closed KNOWN_BUGS #5, #6, #8.
- **`ae7d7be`/`346af89`** — cgroup isolation: each lead in its own transient `systemd-run` unit.
  Closed the channel-restart-kills-leads problem.
- **`4082e3b`** — poller-hijack hardening merged to main (manual-shell + lead vectors covered;
  subagent vector verification is the remaining BUG-7 task above).
