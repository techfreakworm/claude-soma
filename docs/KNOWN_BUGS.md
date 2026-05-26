# Known Bugs

Living list of known, unresolved (or only partially resolved) bugs in Claude Soma.
Each entry records symptom, trigger, mechanism, status, and pointers.

---

## 1. New Claude sessions on the VPS hijack the Telegram poller / restart the orchestrator

- Status: OPEN (partial fix shipped, covers orchestrator-spawned project leads only)
- Severity: high - silently drops the Telegram bot, or restarts the whole channel
- Documented: 2026-05-25 (project-lead case), 2026-05-26 (general case)

### Symptom

The main Telegram orchestrator (the persistent `claude --channels` session running in
tmux session `hermes`) suddenly stops responding on Telegram, or the entire
`claude-soma-channel.service` restarts. A channel restart also kills every background
subagent / project lead in its cgroup (see the orchestrator lead-liveness note).

### Trigger

Any NEW `claude` session on the VPS that loads the user-enabled
`telegram@claude-plugins-official` plugin. Known triggers:

- A user SSHes into the VPS and runs `claude` interactively. It loads user-scope
  `~/.claude/settings.json`, where the telegram plugin is enabled.
- Any spawned new claude session that auto-connects to the telegram MCP - e.g. an
  Agent/Task subagent dispatched by the orchestrator. (Confirmed 2026-05-26:
  dispatching a background Agent dropped the bot's Telegram connection within seconds
  and the channel was restarted, killing the agent.)

### Mechanism

The telegram plugin boots a `bun server.ts` grandchild. On startup that bun process
reads `/home/ubuntu/.claude/channels/telegram/bot.pid`, finds the main bot's poller
PID, `SIGTERM`s it (logs "replacing stale poller pid=N" at server.ts:65-66), and writes
its own PID into the file. The main session keeps running, but its bun poller is now
dead and Claude Code does NOT auto-restart it, so the bot goes deaf. Claude Code's
`--channels` filter only gates notification *routing* - it does not stop a non-channel
session from booting the plugin's MCP server and running the takeover logic.

Separately, the healthcheck (commit `9bf10d0`) notices the missing bun MCP child and
restarts `claude-soma-channel.service` - turning a silent poller hijack into a full
channel restart that also takes down any running subagents / project leads.

### Expected behaviour

A new claude session must NOT take over the Telegram poller or cause the orchestrator
to restart. Only the designated `--channels` bot session should own the poller.

### Partial fix already shipped

`1e4dbd3` / `b33eec5` ("project leads skip user-scope settings to avoid stealing bot
poller"): orchestrator-spawned project leads now launch with
`--setting-sources project,local`, which skips user-scope `~/.claude/settings.json`
(where `enabledPlugins` lives) so they never load the telegram plugin.
(`--no-plugins` does not exist in Claude Code 2.1.150.)

This covers project leads ONLY. It does NOT cover:
- manual `claude` sessions started by a logged-in user, and
- Agent/Task subagents spawned by the orchestrator.

### Workaround (until fully fixed)

- Do not run a bare `claude` from the user shell on the VPS while the bot is live.
  If you must, start it with `claude --setting-sources project,local` (from a cwd that
  is not `/opt/claude-soma`) so it does not load the user-scope telegram plugin.
- The orchestrator should avoid dispatching Agent/Task subagents that auto-load the
  telegram MCP; prefer doing such work inline in the bot session until a harness-level
  skip is available for subagents.

### Possible real-fix directions (not yet implemented)

- Make the bun poller refuse to take over unless it is the designated channel consumer
  (gate the `bot.pid` takeover on an env var only the bot session sets, or no-op when
  the plugin is loaded by a session whose `--channels` list does not include telegram).
- Apply the plugin-skip (`--setting-sources project,local` or equivalent) to ALL
  non-bot session entry points, including Agent/Task subagents - needs a harness-level
  hook, since Agent dispatches do not expose setting-sources.

### Pointers

- Investigation + evidence: `docs/notes/2026-05-25-telegram-poller-race.md`
- Poller-takeover code: the telegram plugin's `server.ts` (~lines 60-66)
- PID file: `/home/ubuntu/.claude/channels/telegram/bot.pid`
- Healthcheck restart behaviour: commit `9bf10d0`, `scripts/healthcheck.sh`
