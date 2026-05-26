# Known Bugs

Living list of known, unresolved (or only partially resolved) bugs in Claude Soma.
Each entry records symptom, trigger, mechanism, status, and pointers.

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
