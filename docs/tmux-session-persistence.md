# tmux session persistence: evaluating tmux-resurrect / tmux-continuum

Question: can tmux-resurrect + tmux-continuum "resurrect killed tmux sessions amidst
channel restarts" - i.e. bring back the bot channel and its project leads after
`claude-soma-channel.service` restarts?

Short answer: **partially, and it is not the right primary fix.** The plugins can
re-create the tmux session/window/pane structure and re-launch processes, but they cannot
restore a claude session's conversation state, they conflict with how the channel session
is systemd-managed, and they do not address the actual root causes. Details and the fixes
that would actually help are below.

## What the two plugins do

- **tmux-resurrect**: saves a snapshot of the tmux server - sessions, windows, panes,
  layouts, working directories, and (only for programs listed in `@resurrect-processes`)
  the command line to re-run on restore. Manual: `prefix + Ctrl-s` saves,
  `prefix + Ctrl-r` restores. It restores the literal saved command line; it does NOT
  preserve a program's in-memory state.
- **tmux-continuum**: builds on resurrect - auto-saves every ~15 minutes and, with
  `@continuum-restore 'on'`, automatically restores the last snapshot when the tmux
  **server** next starts. Can also auto-start tmux at boot.

## How Claude Soma actually runs tmux (the constraints)

From `systemd/claude-soma-channel.service`:
- The unit is `Type=oneshot` + `RemainAfterExit=yes`. On start it runs
  `tmux kill-session -t hermes` (ExecStartPre) then
  `tmux new-session -d -s hermes ... claude --channels ...` (ExecStart); on stop it runs
  `tmux kill-session -t hermes`.
- So systemd OWNS the `hermes` session lifecycle explicitly - it deletes and recreates
  `hermes` itself on every start.

From `src/claude_soma/mcp_servers/project_orchestrator/spawner.py`:
- Project leads launch as separate sessions in the same tmux server:
  `tmux new-session -d -s soma-proj-<name> ... claude --remote-control <name>
  --setting-sources project,local ... <brief>`.

Because the channel's `tmux new-session` is what starts the per-user tmux **server**, that
server (and every lead session created in it later) lives in the channel service's cgroup.
Restarting `claude-soma-channel.service` tears down the whole server, killing the channel
AND all leads (see the orchestrator lead-liveness note and `docs/KNOWN_BUGS.md`).

## Why resurrect/continuum is a poor fit here

1. **No conversation state.** Resurrect would re-run a lead's saved command line
   (`claude --remote-control ... <brief>`), starting a FRESH claude from the initial
   brief. The in-progress conversation - the whole point of "resurrecting" a lead - is
   lost. Same for the channel. This is "re-launch from scratch," not "resurrect."
2. **Conflicts with the systemd-managed `hermes`.** The unit always recreates `hermes` on
   start. If continuum `@continuum-restore 'on'` also restores a saved `hermes`, you get a
   duplicate/conflicting session and possibly two `claude --channels` processes racing for
   the Telegram poller - the exact failure in `docs/KNOWN_BUGS.md` #1.
3. **Does not fix the root causes.** The pain is (a) the channel getting restarted at all
   (often by the poller-hijack bug via the healthcheck), (b) leads not being resumable, and
   (c) the registry still reporting dead leads as active. Resurrect addresses none of these.

## What would actually help (recommended, in priority order)

1. **Stop the spurious restarts** - fix the Telegram poller-hijack (`docs/KNOWN_BUGS.md`
   #1) so new sessions stop killing the bot poller and tripping the healthcheck restart.
   Highest leverage: if the channel is not restarting, leads are not dying.
2. **Make leads resumable** - launch leads through a wrapper that, on restart, resumes the
   most recent conversation (`claude --continue` / `--resume`) for that session's cwd
   instead of starting fresh, persisting the session id with the registry row. This gives
   real continuity, which resurrect cannot.
3. **Reconcile the registry with reality** - on channel start, mark as dead any registry
   lead whose `tmux has-session -t soma-proj-<name>` fails (the lead-liveness note already
   prescribes trusting tmux, not the registry).

## If you still want resurrect (limited, manual, opt-in)

Use it only as an operator convenience for MANUAL tmux windows you set up when you
`tmux a -t hermes` to watch - NOT to auto-restore `hermes` or the leads. Keep
`@continuum-restore 'off'` so it never fights the systemd-managed session. A minimal
config is provided at `config/tmux/tmux.conf` (manual save/restore via the resurrect
keybindings; continuum auto-restore left OFF on purpose). To use it: install TPM, copy the
config to `~/.tmux.conf`, then `prefix + I` to fetch plugins - in a controlled way, never
by killing the live `hermes` session.

Verdict: evaluated and documented; intentionally NOT wired into the autonomous
channel/lead lifecycle. Pursue the three fixes above for the actual resilience goal.
