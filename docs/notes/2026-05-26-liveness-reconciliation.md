# Registry liveness reconciliation (V1.5 follow-up)

2026-05-26. Secondary issue from `TASK-cgroup-isolation.md`: `list_projects` /
`get_status` reported a lead's registry `status` as `active` even after the lead
had died. Confirmed live: `soma-improver` showed `active` while its unit was
`inactive` and its tmux socket was gone; `hello-world` likewise died (stale
socket file, no server) yet the registry still called it active.

## Why it lied

The registry only ever learned a lead died if an operator called `kill_project`
(which sets `status='killed'`). A lead that vanished on its own -- a channel
restart before cgroup isolation, a crash, or simply finishing -- left its row at
`status='active'` forever.

## The liveness signal: tmux has-session, NOT systemd

`is_lead_alive(name)` (in `spawner.py`) runs:

```
tmux -L soma-lead-<name> has-session -t soma-proj-<name>
```

- Exit 0 -> alive. Non-zero -> dead. This is **ground truth**: the lead's claude
  process *is* the tmux pane process, and with `remain-on-exit` off (our default)
  tmux destroys the session the instant claude exits.
- `has-session` actually connects to the server, so a **stale socket file left
  behind by a dead server does not fool it** (verified live on `hello-world`:
  socket file present, `has-session` -> "no server running", exit 1).

We deliberately do **not** trust `systemctl is-active`: the lead's transient unit
is `Type=oneshot` + `RemainAfterExit=yes`, so it reads `active (exited)` even
after the tmux server inside it has died. `is-active` would call a dead lead
alive -- the exact bug.

### Conservative on tool error

If the check itself can't run (tmux missing, timeout), `is_lead_alive` returns
**True**. A false `dead` would hide a running lead from `list_projects` and risk
a duplicate respawn -- worse than briefly showing a ghost.

## Reconciliation

`server._reconcile_active()` cross-checks every `active` row and flips the dead
ones to `status='dead'` (via `set_status(..., bump_activity=False)` so the
demotion doesn't reset the idle clock). It is used by:

- `list_projects_impl` -- dead leads drop out of the listing.
- `get_status_impl` -- a vanished lead reports (and persists) `dead`.
- `spawn_project_impl` -- ghost leads no longer count against `MAX_CONCURRENT`,
  so a dead lead can't wedge the concurrency cap and block a real spawn.

New status vocabulary: `dead` = vanished on its own, distinct from `killed` =
operator-initiated via `kill_project`.
