# Project-lead cgroup teardown: isolating leads from the channel service

Date: 2026-05-25
Status: implemented (P0)

## The bug

Any restart of `claude-soma-channel.service` — healthcheck watchdog, manual, or
deploy-triggered — instantly kills EVERY running project lead.

On 2026-05-25 the healthcheck restarted the channel at 18:25:10 ("bun MCP
missing"), ~2.5 min after a deploy ran `sudo systemctl restart
claude-soma-api.service`. That channel restart wiped both running leads
(soma-improver, mayank-portfolio).

## Root cause (verified)

- The channel unit has `KillMode=control-group` (systemd default).
- The tmux SERVER, the bot's claude, and every MCP child all live in ONE
  cgroup: `/system.slice/claude-soma-channel.service`.
- The orchestrator spawned a lead with
  `tmux new-session -d -s soma-proj-<name> ...` on the DEFAULT tmux socket.
  That attaches to the SAME tmux server the channel started, so the lead's
  session and its claude process land in the channel's cgroup too.
- When systemd stops the channel it SIGKILLs the whole cgroup, the single
  shared tmux server dies, and every session (`hermes` + every `soma-proj-*`)
  dies with it. ExecStart then starts a fresh tmux server with only `hermes`.

Constraint to preserve: claude needs a real PTY (it drops to `--print` mode on
a pipe), which is why tmux is used. Any fix must keep a PTY for the lead.

## Decision

Spawn each lead inside its own **transient systemd service**, created at runtime
with `systemd-run`, owning a **dedicated tmux socket**:

```
sudo -n systemd-run --collect --quiet \
  --unit=claude-soma-lead-<name>.service \
  --property=Type=oneshot --property=RemainAfterExit=yes \
  --property=User=ubuntu --property=Group=ubuntu \
  --property=EnvironmentFile=-/etc/claude-soma/secrets.env \
  --setenv=HOME=/home/ubuntu \
  --setenv=PATH=<lead PATH> \
  --setenv=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
  -- /usr/bin/tmux -L soma-lead-<name> new-session -d -s soma-proj-<name> \
     -c <cwd> <claude argv...>
```

Each lead therefore gets its OWN cgroup `/system.slice/claude-soma-lead-<name>.service`,
a sibling of (not a child of) `claude-soma-channel.service`. Because
`KillMode=control-group` only reaches a unit's own cgroup subtree, restarting
the channel can no longer touch a lead. The tmux server is born INSIDE the new
unit (we do not merely change sockets), so it is parented to the lead's cgroup,
not the orchestrator's.

### Why this over the alternatives

- **`claude-soma-lead@<name>.service` template unit** — robust, but a template
  cannot carry a per-spawn brief/cwd/flags without an extra EnvironmentFile or
  wrapper per lead, and the unit file must be deployed + enabled ahead of time.
  More moving parts, harder to test hermetically.
- **`systemd-run --scope`** — gives a per-lead cgroup and auto-GCs when the tmux
  server dies (nice for liveness), BUT a scope is just a cgroup container: it
  has no `EnvironmentFile=`/`Environment=` of its own, so the only way to pass
  `CLAUDE_CODE_OAUTH_TOKEN` is on the command line, which leaks the secret via
  `ps`/audit. Rejected on that basis (no API key, Max OAuth only — the token is
  sensitive).
- **Single long-lived `claude-soma-leads.service` owning one `-L leads`
  server** — simplest orchestrator change, but couples every lead to one extra
  service and one extra cgroup; if `tmux -L leads` is not already running the
  orchestrator would silently auto-start it inside the channel cgroup,
  re-introducing the bug. Rejected: too easy to footgun.

The transient-service approach is **file-less** (nothing to deploy/enable), runs
the lead as `ubuntu`, loads secrets via `EnvironmentFile` (no secret on argv),
and mirrors the channel's own proven `Type=oneshot` + `RemainAfterExit=yes`
pattern (tmux daemonizes too deeply for `Type=forking`/`exec` to track a
MainPID — the channel unit documents the same finding).

## Environment passed to a lead

Previously the lead inherited the channel's full environment implicitly (tmux
server inherited the bot claude's env). A fresh systemd unit does NOT inherit
that, so we restore the essentials explicitly:

- `EnvironmentFile=-/etc/claude-soma/secrets.env` — `CLAUDE_CODE_OAUTH_TOKEN`,
  `AUTH_GITHUB_*`, etc. The leading `-` makes it optional so spawn does not fail
  on a box without the file (e.g. CI/dev).
- `HOME=/home/ubuntu` — claude reads `~/.claude.json`, credentials, native binary.
- `PATH` — systemd's default omits `~/.local/bin` and the venv; we set a full
  PATH so claude and the tools it shells out to (git, gh, node, bun, npm) resolve.
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` — leads spawn team scaffolds.

## Kill / cleanup

`kill_session` best-effort `systemctl stop claude-soma-lead-<name>.service`
(tears down the whole cgroup, tmux server included) and then best-effort
`tmux -L soma-lead-<name> kill-session -t soma-proj-<name>`. Both tolerate
"already gone". Stopping the unit prevents leaking `active (exited)` units.

## Known limitations / follow-ups

- `Type=oneshot`+`RemainAfterExit` does not auto-GC: a lead whose claude exits
  on its own (crash, not via `kill_project`) leaves the unit `active (exited)`.
  Liveness reconciliation (separate follow-up) should cross-check
  `systemctl is-active` / `tmux has-session` and reap such units. Until then,
  re-spawning a name whose stale unit still exists fails loudly (systemd-run
  reports the unit already exists) rather than silently — run `kill_project`
  first.
- Per-lead logging (`pipe-pane` to `/var/log/claude-soma/<name>.log`) and the
  empty `rc_url` capture-regex fix are tracked separately.

## Deployment

No new unit file to install. Requires the orchestrator's user (`ubuntu`) to run
`systemd-run`/`systemctl` via passwordless sudo, which is already configured on
the VPS (`ubuntu` has `NOPASSWD: ALL`). The healthcheck that restarts the
channel is unchanged and now harmless to leads.

## Test

`tests/mcp_servers/test_orchestrator_cgroup_isolation.py` spawns a throwaway
lead (with a stub `claude` that just sleeps) through the real spawn path and
asserts its tmux server runs in `/system.slice/claude-soma-lead-<name>.service`
and NOT in `claude-soma-channel.service` — the exact property that makes a
channel restart survivable. The test self-skips where systemd + passwordless
sudo are unavailable, and stops the unit on teardown. It deliberately does not
restart the real channel (that would kill the live bot and any running leads).
