# Known Bugs

Living list of known, unresolved (or only partially resolved) bugs in Claude Soma.
Each entry records symptom, trigger, mechanism, status, and pointers.

Status: planning (awaiting approval) for the newly-added entries (#3–#9) and the
"Resolved recently" section. Date of this pass: 2026-05-27. Entries #1–#2 are unchanged.
Severity scale: **P0** (breaks the product / data loss) · **P1** (major feature broken or
silent failure) · **P2** (degraded / annoying) · **P3** (minor / cosmetic / forensic).
Only evidenced issues are listed; suspected-but-unverified risks are labelled **suspected**.

---

## 1. New Claude sessions on the VPS hijack the Telegram poller / restart the orchestrator

- Status: OPEN (manual-shell + project-lead fixes shipped; structural root fix for the
  subagent vector implemented on branch fix/telegram-poller-hijack-hardening, pending a
  maintenance-window deploy + one inheritance check)
- Severity: high - silently drops the Telegram bot, or restarts the whole channel
- Documented: 2026-05-25 (project-lead case), 2026-05-26 (general case + manual-shell
  evidence + plugin-load semantics verified)

### Symptom

The main Telegram orchestrator (the persistent `claude --channels` session running in
tmux session `hermes`) suddenly stops responding on Telegram, or the entire
`claude-soma-channel.service` restarts. A channel restart historically also killed every
background subagent / project lead sharing its cgroup (see bug-context note below and
`docs/notes/2026-05-25-project-lead-cgroup-teardown.md`).

### Trigger

Any NEW `claude` session on the VPS that loads the user-enabled
`telegram@claude-plugins-official` plugin. Known, confirmed triggers:

- A user SSHes into the VPS and runs a bare `claude`. It loads user-scope
  `~/.claude/settings.json`, where the telegram plugin is enabled. Confirmed live on
  2026-05-26: PID 88094 (`claude --remote-control`, cwd `/home/ubuntu`, launched with
  no `--setting-sources`) loaded the plugin at 05:54 (its `-home-ubuntu`
  telegram mcp-log is dated that minute); the 05:59 channel restart's new bun reclaimed
  the slot, leaving 88094 with a dead poller.
- An Agent/Task subagent dispatched by the bot. This is the bot's PRIMARY workflow, not
  an edge case: `system_prompts/responsive_bot.md` instructs the bot to background-
  dispatch a `general-purpose` Agent for ANY task over ~3 tool calls ("when in doubt,
  dispatch"). With `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` each dispatch is a fresh
  plugin-loading `claude` session, so hijacks recur by design. (Confirmed 2026-05-26:
  dispatching a background Agent dropped the bot's Telegram connection within seconds
  and the channel was restarted, killing the agent.)

### Mechanism

The telegram plugin boots a `bun server.ts` grandchild. On startup that bun process
(`~/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6/server.ts`):

1. resolves the bot token from `process.env.TELEGRAM_BOT_TOKEN`, OR self-loads it from
   `$TELEGRAM_STATE_DIR/.env` (default `~/.claude/channels/telegram/.env`, server.ts:31).
   So a manual session needs NO token in its own env to hijack -- loading the plugin is
   sufficient.
2. reads `PID_FILE = $TELEGRAM_STATE_DIR/bot.pid` (default
   `~/.claude/channels/telegram/bot.pid`); if it holds a LIVE pid it `SIGTERM`s it and
   takes the slot (server.ts:62-66, "replacing stale poller pid=N"), then writes its own
   pid (server.ts:69). There is NO gate for "am I the designated `--channels` consumer".

The bot's `claude` keeps running, but its bun poller is now dead and Claude Code does
NOT auto-restart a dead stdio MCP child, so the bot goes deaf. Separately the healthcheck
(commit `9bf10d0`) notices the missing bun child and `systemctl restart`s
`claude-soma-channel.service` - turning a silent poller hijack into a full channel
restart.

### Verified plugin-load semantics (2026-05-26) - rules out the "obvious" fix

Tested in full isolation (fake token + throwaway `TELEGRAM_STATE_DIR` + throwaway tmux
socket, so it could not touch the live bot): launching
`claude --channels plugin:telegram@claude-plugins-official --setting-sources project,local`
(i.e. telegram NOT in the loaded `enabledPlugins`) prints the banner
"Listening for channel messages from: plugin:telegram@..." but DOES NOT boot the plugin's
MCP server -- no `bun`, no `bot.pid` written, no `mcp-logs-plugin-telegram-telegram` dir.

Implication: `--channels` is only notification *routing*. The plugin's poller boots ONLY
when `telegram` is enabled in a settings scope the session actually loads. Therefore
removing telegram from user-scope `enabledPlugins` and "relying on `--channels` to load
it for the bot" would SILENTLY BREAK THE BOT (banner shown, but no poller). Any real fix
must keep telegram enabled in some scope the bot loads.

### Partial fixes already shipped

- `1e4dbd3` / `b33eec5`: orchestrator-spawned project leads launch with
  `--setting-sources project,local`, skipping user-scope `enabledPlugins` so they never
  load the plugin. Covers project leads ONLY. (`--no-plugins` does not exist in Claude
  Code 2.1.150; `--bare` disables plugins but forces `ANTHROPIC_API_KEY` auth, which is
  unusable here -- Max OAuth only.)
- `scripts/claude-safe.sh` (this branch): a wrapper for interactive/manual `claude` on
  the VPS. Injects `--setting-sources project,local` (and a throwaway
  `TELEGRAM_STATE_DIR`) for any non-`--channels` invocation, passing the bot's own
  `--channels` command and management subcommands through untouched. Installed by
  `vps_bootstrap.sh` as `/usr/local/bin/claude-safe` and shadows interactive `claude`
  for the `ubuntu` user. Covers the manual-shell vector.

Still NOT covered: Agent/Task subagents dispatched by the bot.

### Blast-radius mitigation (separate, complementary)

Branch `fix/project-lead-cgroup-isolation` runs each project lead in its own transient
`systemd-run` unit (sibling cgroup) on a dedicated tmux socket, so a channel restart
(`KillMode=control-group`) can no longer kill leads. This does not stop the hijack but
removes its worst consequence. See `docs/notes/2026-05-25-project-lead-cgroup-teardown.md`.

### Root fix (implemented on branch, pending maintenance-window deploy)

Verified by isolated probing (2026-05-26): `--channels` does NOT load the plugin without
`enabledPlugins` in a loaded scope; a bot-only `--settings <file>` DOES load it and merges
additively with the bot's user-scope settings. So the fix removes telegram from user scope
and has the bot opt in via `--settings`:

- `scripts/disable-user-telegram-plugin.sh` removes telegram from user-scope enabledPlugins
  (idempotent, backs up). No non-bot session then loads the plugin from the default scopes.
- `scripts/channel-claude.sh` adds `--settings config/claude/channel-settings.json`
  (telegram-only) to the bot launch, so only the bot re-acquires the plugin.

This covers manual shells AND subagents regardless of whether subagents inherit
`--setting-sources` (both the default scopes and the cleaned user scope are telegram-free).
The single residual -- whether Claude Code propagates the parent's `--settings` flag to
Agent/Task subagents -- plus the deploy and a bot-still-polls check are validated in a
maintenance window. Full design + window checklist:
`docs/notes/2026-05-26-telegram-plugin-scope-isolation.md`.

Fallback if that residual fails (subagents DO inherit `--settings`): route the bot's heavy
work to orchestrator-spawned leads (plugin-skipped + cgroup-isolated) via
`system_prompts/responsive_bot.md` instead of in-session Agent dispatches. (A third option,
patching the third-party plugin to gate its takeover on an env var only the bot sets, is a
last resort -- it is erased on plugin upgrade.)

Note: a distinct `TELEGRAM_STATE_DIR` per non-bot session prevents the `SIGTERM` of the
real `bot.pid`, but a session that still has a real token in its env would then poll the
same bot token and 409-thrash with the bot. It is only safe combined with not loading the
plugin at all -- so it is belt-and-suspenders, not a fix on its own.

### How to test safely

NEVER reproduce against the live `bot.pid`. Always set a throwaway `TELEGRAM_STATE_DIR`
AND a fake `TELEGRAM_BOT_TOKEN`, and run inside a throwaway `tmux -L` socket, so the test
session can neither `SIGTERM` the real poller nor contend for the real token. The
subagent-inheritance question (case 1 vs case 2) additionally needs a throwaway `HOME`
(seeded with credentials + a simulated user-scope `enabledPlugins`) or a maintenance
window, because a teammate that fails to inherit the throwaway env could otherwise reach
the live bot.

### Pointers

- Investigation + evidence: `docs/notes/2026-05-25-telegram-poller-race.md`
- Poller-takeover code: the telegram plugin's `server.ts` (token at :31/:42, takeover at
  :62-66, pid write at :69)
- PID file: `/home/ubuntu/.claude/channels/telegram/bot.pid`
- Manual-shell wrapper: `scripts/claude-safe.sh`, `tests/test_claude_safe_wrapper.py`
- Healthcheck restart behaviour: commit `9bf10d0`, `scripts/healthcheck.sh`

---

## 2. Killed project leads respawn fresh, losing conversation history and their team

- Status: OPEN (not implemented)
- Severity: medium - a killed lead loses all context; the operator must re-brief it
- Documented: 2026-05-26

### Symptom

When a project lead dies (channel restart before the cgroup-isolation fix lands, OOM,
crash, or an explicit `kill_project`), the orchestrator has no way to bring it back with
its prior state. `spawn_background_lead` always starts a brand-new `claude` with a fresh
session, so the lead's conversation history, in-progress task state, and any agent-team
teammates are gone.

### Desired behaviour

A lead that was killed (not deliberately retired) should be respawnable with its history
intact, via `claude --resume <session-id>` (or `--continue`), so it resumes where it left
off instead of starting cold.

### Mechanism / gaps

- `src/claude_soma/mcp_servers/project_orchestrator/spawner.py` has NO resume logic: it
  neither records the lead's claude session id at spawn nor passes `--resume`/`--continue`
  on respawn. The registry tracks a lead by tmux/agent name, not by claude session id.
- Claude Code supports `--session-id <uuid>`, `-r/--resume [id]`, `-c/--continue`, and
  `--fork-session`. To resume deterministically the spawner would need to (a) generate a
  UUID and pass `--session-id <uuid>` at first spawn, (b) persist `name -> uuid` in the
  registry, and (c) on respawn build argv with `--resume <uuid>` (keeping
  `--setting-sources project,local` and the cgroup-isolation wrapper).
- Agent-team teammates are the hard part. With
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` a lead spawns teammates in split tmux panes;
  those are separate live processes, not part of the lead's own transcript. `--resume`
  restores the LEAD's conversation but does NOT restore the live teammates -- when the
  lead died, its team died with it. There is no mechanism today to re-establish a team on
  resume. A resumed lead would see its own history referencing teammates that no longer
  exist.

### Design sketch (for review, not yet built)

1. Spawn: generate `session_id = uuid4()`, add `--session-id <session_id>` to the lead's
   argv, persist it in the registry alongside the agent/tmux name.
2. Respawn-after-death: detect a dead-but-not-retired lead (liveness reconciliation, the
   follow-up noted in the cgroup-teardown design), and re-run the spawn path with
   `--resume <session_id>` instead of a fresh session, into a new transient unit + socket.
3. Team restoration: the open question. Options -- (a) accept teammates are ephemeral; the
   resumed lead re-plans and re-dispatches them from its restored transcript; (b) persist
   the team roster (teammate names + briefs) at dispatch time and have a resume hook
   re-spawn each teammate, then reconcile; (c) declare teams out of scope for v1 resume.
   Recommend (a) for v1 (simplest, correct-ish: the lead's transcript records what the
   team was doing) and revisit (b) if teammate work is too expensive to redo.

### Pointers

- Spawner (no resume today): `src/claude_soma/mcp_servers/project_orchestrator/spawner.py`
- Lead liveness/teardown context: `docs/notes/2026-05-25-project-lead-cgroup-teardown.md`
  ("Known limitations / follow-ups" - liveness reconciliation)
- Claude Code flags: `--session-id`, `--resume`, `--continue`, `--fork-session`

---

## 3. `kill_project` leaves the lead's tmux server + transient unit running

- Status: RESOLVED — see Resolved recently (commits `238f78f`, `c99c743`); hardened with post-kill liveness verification + retry in `c99c743`
- Severity: P1 - the operator believes a project is gone; it is still consuming RAM and a
  cgroup, and the name can't be re-spawned without a manual `systemctl stop`
- Documented: 2026-05-27 (tracked since the spawner rewrite as CHECKLIST item #36)

### Symptom

Telling the bot "shut down <name>" marks the registry row `status='killed'` but the lead's
`claude` process, its dedicated tmux server, and its transient
`claude-soma-lead-<name>.service` keep running. A later re-spawn of the same name fails
loudly ("a systemd unit for project <name> already exists").

### Mechanism

The spawner gained `kill_session()` (best-effort `systemctl stop` of the unit + `tmux
kill-session` on the lead's socket) during the cgroup-isolation work, but
`project_orchestrator/server.py::kill_project_impl` was never updated to call it — it still
only does `set_status(name, 'killed')`. So the registry and reality diverge.

### Fix

~10-line change: have `kill_project_impl` call `spawner.kill_session(name)` before (or after)
flipping the registry status. Liveness reconciliation (note below) already demotes a
vanished lead to `dead`, but a *deliberately* killed lead should be torn down immediately, not
left for the reconciler.

### Pointers

- `src/claude_soma/mcp_servers/project_orchestrator/server.py` (`kill_project_impl`)
- `src/claude_soma/mcp_servers/project_orchestrator/spawner.py` (`kill_session`, already present)
- Tracking note: `docs/CHECKLIST.md` -> "V1.5 backlog" #36

---

## 4. The routines registry table is never populated (dashboard `created_by` is synthesized)

- Status: OPEN (not implemented)
- Severity: P2 - `/api/routines` still renders (it falls back to synthesized entries), but
  provenance is wrong: a bot- or user-created routine never shows canonical `bot`/`user`
- Documented: 2026-05-27 (CHECKLIST items #37/#38-adjacent)

### Symptom

`/api/routines` aggregates registry + systemd timers + cron + cloud, but the **registry**
source is always empty because nothing calls `register_routine()` at creation time. Every row
therefore comes from the synthesized systemd/cron/cloud sources, so `created_by` is always
`system`/`cron`/`cloud` — never the canonical `bot` or `user`.

### Mechanism / gaps

Three call sites need to register on creation:
1. `skills/schedule-routine/` — after creating a cloud RemoteTrigger, call
   `register_routine(name, kind="cloud", ..., created_by="user")`.
2. Bot-created local timers (e.g. `portfolio-oneliner`) — `created_by="bot"`.
3. `soma-init` wizard — `created_by="system"` for the 4 default timers. (The wizard *does*
   call a `_backfill_default_routines()` helper, but only the wizard path runs it; bot- and
   user-created routines remain unrecorded.)

A related sub-item: store the systemd unit name in `metadata.unit` so the merger stops relying
on heuristic `<name>` ↔ `claude-soma-<name>.timer` aliasing.

### Pointers

- `src/claude_soma/api/routes/routines.py` (the merger)
- `src/claude_soma/mcp_servers/project_orchestrator/registry.py` (`register_routine`)
- `src/claude_soma/wizard/init.py` (`_backfill_default_routines`)
- Aggregation/perf context: `docs/notes/2026-05-26-routines-aggregate-and-perf.md`

---

## 5. Per-lead logs (and channel/api logs) grow unbounded — no rotation

- Status: RESOLVED — `scripts/logrotate-claude-soma` delivered; bootstrap step installs it at `/etc/logrotate.d/claude-soma` (daily, 14 rotations, 50 MB size cap, `copytruncate`)
- Severity: P2 - a long-lived lead or a busy channel can fill `/var/log/claude-soma` over time
- Documented: 2026-05-27 (follow-up flagged in the per-lead-logging note)

### Symptom

`/var/log/claude-soma/<name>.log` (per lead), `channel.log`, `api.log`, `healthcheck.log`,
etc. are append-only with no logrotate stanza. A lead that runs for days writes raw PTY bytes
continuously.

### Mechanism

Per-lead logging tees the pane via `tmux pipe-pane … cat >> <name>.log`; nothing truncates or
rotates it. The channel log is similarly an append `pipe-pane`.

### Fix

Add a logrotate config for `/var/log/claude-soma/*.log` (size- or time-based, `copytruncate`
since the writers hold the fd open). Low effort.

### Related caveat (P3, forensic)

Those logs are **raw PTY bytes** — full-screen TUI escape sequences and redraws, not clean
text. They are forensic ("what did the lead say before it died"), not readable; pipe through
`cat -v` / an ANSI stripper. A clean transcript would need claude-side support.

### Pointers

- `docs/notes/2026-05-26-per-lead-logging.md` ("Known caveats / follow-ups")
- `spawner.py` (`_lead_log_path`, the `pipe-pane` chain), `HERMES_LEAD_LOG_DIR`

---

## 6. whisper model mismatch: `.mcp.json` expects `base.en`, bootstrap builds `large-v3-turbo`

- Status: RESOLVED — bootstrap step 13/15 now downloads `base.en` by default; `large-v3-turbo` is opt-in via `WHISPER_INCLUDE_LARGE=1` or `--with-large-whisper`
- Severity: P1 if it bites (voice STT fails to start on a fresh box), P3 if the operator
  happens to have both models
- Documented: 2026-05-27

### Symptom (predicted)

On a freshly bootstrapped box, `voice-stt` is configured to load
`/opt/whisper.cpp/models/ggml-base.en.bin` (`.mcp.json` `HERMES_WHISPER_MODEL`), but
`scripts/vps_bootstrap.sh` step 13/15 only downloads
`/opt/whisper.cpp/models/ggml-large-v3-turbo.bin`. If `base.en` is absent, transcription
errors at first use.

### Mechanism

The STT default was switched to `base.en` (English-only, ~13× faster — commit `3322e96`) and
`.mcp.json` + `voice_stt/server.py` point at it, but `vps_bootstrap.sh` still references the
older `large-v3-turbo` model it was written against. The live VPS (per `docs/CHECKLIST.md`)
was provisioned with `large-v3-turbo`, which is why this hasn't surfaced there yet — but a
clean bootstrap would land the wrong model for the current config.

### Fix

Make bootstrap download `base.en` (the documented default) and optionally `large-v3-turbo`
behind a flag; or have `voice_stt` fall back across both. Reconcile the three references:
`.mcp.json`, `scripts/vps_bootstrap.sh`, and `NEXT.md` B4 (which already documents `base.en`
as default + `large-v3-turbo` as optional).

### How to verify

On a box with only `large-v3-turbo`, unset/leave `HERMES_WHISPER_MODEL` at its `.mcp.json`
default and send a voice note; expect a "model not found" style failure from `whisper-cli`.

### Pointers

- `.mcp.json` (`voice-stt` → `HERMES_WHISPER_MODEL=/opt/whisper.cpp/models/ggml-base.en.bin`)
- `scripts/vps_bootstrap.sh` step 13/15 (downloads `large-v3-turbo`)
- `NEXT.md` B4 (correct intent: `base.en` default)

---

## 7. Telegram poller-hijack: subagent vector still open (residual of bug #1)

- Status: OPEN — mitigated for manual shells + leads; subagent `--settings` inheritance unverified (needs maintenance-window test)
- Severity: P1 - the bot's PRIMARY workflow (dispatching background Agents) can still drop the
  poller if `--settings` turns out to be inherited by subagents
- Documented: 2026-05-26 (carried forward here for visibility; see bug #1 for the full writeup)

### Why this is called out separately

Bug #1 above documents the hijack and its shipped fixes in full. This entry exists so a
re-reader doesn't assume #1 is closed: the **one residual** is whether Claude Code propagates
the parent's `--settings` flag to Agent/Task subagents. If it does, a dispatched subagent
reads `channel-settings.json`, loads telegram, and re-introduces the hijack. This is the bot's
main path ("when in doubt, dispatch"), not an edge case.

### Status of verification

Must be confirmed in a maintenance window (the bot restarts): dispatch one trivial background
Agent and watch ~30 s for a second `bun server.ts` / a changed `bot.pid`. If a second bun
appears, pivot to routing heavy work through orchestrator-spawned leads (already
plugin-skipped + cgroup-isolated) via `system_prompts/responsive_bot.md`.

### Pointers

- Full entry + fix design: bug #1 above; `docs/notes/2026-05-26-telegram-plugin-scope-isolation.md`
  ("Residual to verify in the maintenance window")
- `scripts/channel-claude.sh` (`--settings` opt-in), `system_prompts/responsive_bot.md` (fallback)

---

## 8. Playwright social auth rots silently; "needs re-auth" is not surfaced to the user

- Status: RESOLVED — healthcheck extended to scan `~/.claude-pw/NEEDS_REAUTH-*` sentinels and append a DM to `broadcast.jsonl`; deduped per-platform per-day
- Severity: P2 - a scheduled or ad-hoc social post fails at a login wall with no prior warning
- Documented: 2026-05-27 (follow-up from the shared-playwright-auth note)

### Symptom

When a platform's session cookie expires, `pw-refresh.js` correctly declines to overwrite the
good `state-<platform>.json` and drops a `~/.claude-pw/NEEDS_REAUTH-<platform>` sentinel +
a journal line — but nothing tells the user. The first they learn of it is a failed post.

### Fix

Have the healthcheck (or a small bot routine) notice the sentinels and DM the user "X needs
re-auth — VNC in and run `pw-login`." Low effort; explicitly called out as a follow-up in the
note.

### Pointers

- `docs/notes/2026-05-26-shared-playwright-auth.md` ("Surfacing needs re-auth")
- `scripts/pw-refresh.js` (writes the sentinel), `scripts/pw-login.js` (the re-auth flow)

---

## 9. T1 project-spawn end-to-end is only soft-verified after the spawner rewrite

- Status: needs-verification (the rewrite that fixes the original blocker is merged; the live
  end-to-end T1–T5 checklist tests have not been re-run green)
- Severity: P2 - core "build me X" flow is believed working but unproven on the live bot
- Documented: 2026-05-27 (reconciling `docs/CHECKLIST.md` "Pending" against `git log`)

### Background

The checklist records T1 (project spawn) as "soft pass via fallback; orchestrator path
BLOCKED" because Claude Code 2.1.150 removed `claude --bg`. That blocker is **fixed** in code:
the spawner was rewritten to tmux-wrapped per-project sessions (commit `d31df9a`) and further
hardened (cgroup isolation, `--` brief guard, RC-URL capture). What remains is that the live
acceptance tests T1–T5 (spawn → status → kill → message → schedule) in `docs/CHECKLIST.md` are
still marked Pending and have not been re-run against the deployed bot since the rewrite.

### What to do

Re-run T1–T5 from Telegram against the live bot and update the checklist. Note that T3 (kill)
will *also* exercise bug #3 above — until `kill_session()` is wired in, a "killed" project's
unit/tmux survives, so T3's verification (`status='killed'`) passes in the registry while the
process lingers.

### Pointers

- `docs/CHECKLIST.md` "Verification tests" → T1–T5 (Pending)
- Spawner rewrite: commit `d31df9a`; `spawner.py`

---

## 10. Channel-claude stalls inbound messages while bot processes a large attachment

- Status: OPEN — multiple hypotheses, none confirmed yet
- Severity: P1 (medium-high) — silent channel stalls erode trust; the bot appears unresponsive while the user expects acknowledgment
- Documented: 2026-05-29

### Symptom

At 21:38–21:40 UTC on 2026-05-29, the user uploaded a 235 MB pptx file via Telegram intended
for the `ppt-manager` lead. The bot attempted `download_attachment` and received HTTP 400
"file is too big" (the Telegram Bot API `getFile` endpoint caps at ~20 MB). The user's
follow-up text messages sent during that window ended up marked **"Request interrupted by
user"** — the user had to forcefully stop them because the channel was effectively
non-responsive during the failed download attempt.

### Mechanism (hypotheses — investigate)

- (a) The `download_attachment` MCP call kept the channel busy or blocked the inbound queue
  during the failed download attempt
- (b) The `getFile` request timed out slowly and held up the bot's tool-call loop
- (c) A retry loop on the failed download (telegram plugin / `grammy` retries) kept the bot
  occupied
- (d) Bot was mid-dispatch of a long Agent and the inbound poller fell behind

### Investigation pointers

- `/var/log/claude-soma/channel.log` around 21:38–21:40 UTC 2026-05-29 — look for
  `download_attachment` calls, retry patterns, `getUpdates` gaps
- The plugin source at
  `~/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6/server.ts` — search for
  `download_attachment`, `getFile`, retry / timeout / poller-blocking behavior
- `~/.claude-soma/activity.jsonl` around the same window — look for the gap in tool calls
  during the stall

### Proposed fixes

- Short-circuit `getFile` when document size exceeds the ~20 MB cap BEFORE the round-trip
  (the message metadata includes `file_size`; reject early with a clear error to the user
  instead of hitting the 400)
- Decouple the download path from the inbound poller — run downloads in a background task so
  a stuck or slow download can't block `getUpdates`
- Surface the 20 MB cap in the plugin docstrings and the `download_attachment` tool
  description so the bot and user know upfront

### Related

`FUTURE_IMPROVEMENTS.md` (Dashboard section) tracks an admin **file-dropper** for uploads
>20 MB — drag-drop zone on the per-lead admin page, multipart streaming, manifest, optional
DM ping. That's the workflow path that sidesteps this bug entirely. Fixing #10 stops the
stall; the dropper avoids the failed download altogether.

### Pointers

- `/var/log/claude-soma/channel.log` (the live channel log)
- `~/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6/server.ts` (search for
  `download_attachment`, `getFile`)
- `~/.claude-soma/activity.jsonl` (per-tool-call timing — gaps during the stall)

---

## Resolved recently

So re-readers don't re-chase issues that are already fixed. These were live bugs; they are
**closed** (verified or merged to `main`). Kept short — see the linked note/commit for detail.

| Was | Resolution | Evidence |
|---|---|---|
| Channel bot ended turns with reply as plain assistant text (never reached Telegram); operator saw silence; recurring P1 (observed 2026-06-10, session 9b6fde1c) | Stop hook `scripts/tg_reply_guard.py` wired in `hooks/hooks.json`; blocks and reinjects on attempt 1; auto-relays via Bot API on attempt 2; default MODE=log for safe observe-only rollout | 2026-06-13; `scripts/tg_reply_guard.py`, `hooks/hooks.json`, `system_prompts/responsive_bot.md` (Stop-level reply gate section); plan: `docs/superpowers/plans/2026-06-12-tg-reply-enforcement-plan.md` |
| `kill_project` only set registry `status='killed'`; lead's tmux server + transient unit kept running (RAM + cgroup leak, re-spawn collision) | `kill_project_impl` now calls `kill_session(agent_id)` before flipping registry status | `238f78f`; `src/claude_soma/mcp_servers/project_orchestrator/server.py` (`kill_project_impl`) |
| `kill_project_impl` flipped registry to `killed` even when `kill_session`'s subprocess calls silently failed, leaving alive leads hidden behind a `killed` row (3 zombie leads observed 2026-05-28) | Post-kill `is_lead_alive()` verification + one retry before accepting result; raises `RuntimeError` naming agent_id + unit/socket if lead survives both attempts; registry only updated on confirmed death | `c99c743`; `src/claude_soma/mcp_servers/project_orchestrator/server.py` (`kill_project_impl`) |
| Channel restart killed every running project-lead (shared cgroup) | Each lead now spawns in its own transient `systemd-run` unit + dedicated tmux socket (sibling cgroup) | `ae7d7be`/`346af89`; `docs/notes/2026-05-25-project-lead-cgroup-teardown.md`; test `test_orchestrator_cgroup_isolation.py` |
| Registry reported a vanished lead as `active` forever | `_reconcile_active()` cross-checks `tmux has-session` and flips dead rows to `dead` (distinct from operator `killed`) | `a974011`; `docs/notes/2026-05-26-liveness-reconciliation.md` |
| Dashboard rendered completely unstyled (every `/_next/static/*` 404) | `build_frontend.sh` (standalone static copy) wired into `deploy.sh` + made rebuild-safe | `7f7b729`/`9542e98`; `docs/notes/2026-05-26-dashboard-unstyled-static-assets.md` |
| `/api/routines` slow (~12 s) and missing cron/system timers | Cloud query cached (`HERMES_ROUTINES_CLOUD_TTL`) + parallelized + capped at 30 s; cron + all timers aggregated | `9a76a75`; `docs/notes/2026-05-26-routines-aggregate-and-perf.md` |
| Project spawn broke when `claude --bg` was removed in 2.1.150 | Spawner rewritten to tmux-wrapped sessions; `--` guards the brief from variadic `--mcp-config` | `d31df9a`/`659fed6` (live T1–T5 re-run still pending — see #9) |
| Remote Control URL never captured (regex only matched legacy `rc.claude.com`) | Regex updated for `claude.ai/code/session_*` with the legacy form as fallback; poll-retry loop | `c2a59e8`; `spawner.py` `RC_URL_RX` |
| Leads needed hand-wired per-cwd `.mcp.json` bridges | Leads inherit `user,project,local` + curated `lead-mcp.json` (all MCPs except telegram/hermes-api/orchestrator) | `6ac7ed2`; `docs/notes/2026-05-26-leads-inherit-all-mcps.md` |
| `pw-refresh` falsely reported logged-out sessions as authed (landing-URL heuristic) | Authed-ness now decided by the platform session cookie, not the URL; never overwrites good auth | `920f506`; `docs/notes/2026-05-26-shared-playwright-auth.md` |
| Healthcheck restarted the channel from root (no tmux server) every 10 min, killing leads | Healthcheck checks tmux as `ubuntu`; missing-bun recovery is in-pane (`respawn-pane`), not a destructive `systemctl restart` | `ef18ee3`/`ca758d0` |
| `hermes_api` public-stats returned 500 (socket read via `readline`) | Read the socket response to EOF instead | `20e1fb3` |
| Registry sqlite connection not thread-safe under the API's threadpool | Connection made thread-safe | `44e2c66` |

> Note: KNOWN_BUGS #1 (poller hijack) and #2 (killed-lead resume) remain **OPEN** above; they
> are intentionally NOT in this table. #1's subagent residual is broken out as #7.
