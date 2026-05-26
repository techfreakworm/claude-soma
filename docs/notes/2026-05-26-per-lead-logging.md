# Per-lead pane logging (V1.5 follow-up)

2026-05-26. Secondary issue from `TASK-cgroup-isolation.md`: a project lead's
output was lost the moment it died, leaving nothing to diagnose a crash or a
vanished lead. Now each lead's pane is teed to `/var/log/claude-soma/<name>.log`.

## How

In `spawner.spawn_background_lead`, the lead's pane is piped with tmux
`pipe-pane`, chained into the **same** `tmux new-session` invocation via tmux's
`;` command separator:

```
tmux -L soma-lead-<name> new-session -d -s soma-proj-<name> -c <cwd> <claude ...> \
  ; pipe-pane -O -o -t soma-proj-<name> 'cat >> /var/log/claude-soma/<name>.log'
```

(`-O` = pipe output only, `-o` = no-op if a pipe already exists.)

### Why chain it into the same invocation, not a second `tmux` call

- **Atomic with session birth** — logging starts before the lead emits anything,
  so we don't miss the startup banner.
- **No extra spawn subprocess** — the spawn path stays a single `subprocess.run`,
  so the orchestrator/spawner tests' call sequences are unchanged.
- **Survives a channel restart** — the `cat` writer is forked by the tmux
  *server*, which (post cgroup-isolation) lives in the lead's own transient unit
  cgroup, not the channel's. So the log keeps being written even after the
  channel service restarts, exactly like the lead itself. See
  [2026-05-25-project-lead-cgroup-teardown.md](2026-05-25-project-lead-cgroup-teardown.md).

### Failure handling

Best-effort. The log dir is created (`mkdir -p`) before spawn; if that fails we
swallow the error and still spawn. The pipe-pane chain is always appended — if
the dir is unwritable the chained `cat` simply exits and the pane is unaffected.
The log dir is configurable with `HERMES_LEAD_LOG_DIR` (tests point it off
`/var/log`). `/var/log/claude-soma` is ubuntu-owned, so the orchestrator can
create per-lead files there.

## Known caveats / follow-ups

- **Raw PTY bytes.** claude is a full-screen TUI, so the log contains escape
  sequences and redraws, not clean text. It is forensic, not pretty — pipe
  through `cat -v` / an ANSI stripper to read it. Sufficient for "what did the
  lead say before it died"; a cleaner transcript would need claude-side support.
- **No rotation yet.** A long-lived lead's log grows unbounded. Add a logrotate
  stanza for `/var/log/claude-soma/*.log` (V1.5).
