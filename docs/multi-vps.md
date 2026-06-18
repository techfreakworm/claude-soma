# Multi-VPS orchestration — commanding leads across hosts

Claude Soma can run leads on **additional VPS hosts** while the orchestrator,
registry, FI-NOTIFY listener, relay, and watchdog all stay on the primary host
(**A**). A secondary host (**B**, **C**, …) is a *lead-runtime only* box: it runs
leads on A's command and reports back, but never runs a second orchestrator.

This exists so a memory-hungry or crucial-persistent lead (e.g. a trading system)
can live on its own RAM, physically isolated from sibling leads on A — a spike on
B can't trigger the kernel OOM-killer against leads on A.

> Single-VPS is the default. Everything below is **opt-in**: a fresh install ships
> `hosts.json` with only `local`, and behaves exactly as before until you enroll a host.

---

## Architecture

```
            ┌──────────────────────── VPS-A (orchestrator) ───────────────────────┐
            │  claude --channels  ──>  project_orchestrator MCP                    │
            │      │                      │  registry.sqlite (host + tier columns) │
            │      │                      │  watchdog (tri-state liveness)         │
            │      │                      │  FI-NOTIFY listener :9100 (bearer)     │
            │      │                      └─ RemoteRunner ── ssh -i soma-orchestrator
            └──────┼───────────────────────────────────────────│──────────────────┘
                   │ leads on A (host=local)                    │ Tailscale (100.x)
                   ▼                                            ▼  forced-command guard
            tmux + systemd-run (cgroup MemoryMax)        ┌──── VPS-B (lead-runtime) ────┐
                                                         │ remote-exec-guard.sh         │
                                                         │   builds systemd-run+tmux+   │
                                                         │   claude-safe (User=ubuntu)  │
                                                         │ lead reports -> A listener   │
                                                         └──────────────────────────────┘
```

- **Control channel:** A reaches B over **SSH-on-Tailscale** using a dedicated
  `~/.ssh/soma-orchestrator` key whose `authorized_keys` line on B is pinned to a
  **forced command** — the guard (`scripts/remote-exec-guard.sh`). A can only send
  a fixed verb contract (`spawn`/`resume`/`kill`/`capture`/`list`/`has-session`/
  `rc-url`); it can never run an arbitrary shell. The guard *constructs* the real
  `systemd-run + tmux + claude` argv itself, hardcoding `User=ubuntu`, the unit
  name, and the per-tier `MemoryMax/High`.
- **Per-lead `host` + `tier`:** every lead row carries a host (`local` default) and
  a tier (`standard`|`critical`). Tier maps to a cgroup memory cap. Admission is
  per-host (concurrency cap + sum-of-caps memory).
- **Tri-state liveness:** remote leads are `alive` / `dead` / `unreachable`. A
  momentarily-unreachable host is **never** demoted to dead or revived (no
  split-brain); only a confirmed-dead remote session is acted on.
- **Cross-host FI-NOTIFY:** A's listener is the durable writer (owns the event id,
  bearer-gated, binds `127.0.0.1` + A's tailnet IP — never `0.0.0.0`). A B-lead's
  `notify_orchestrator` is a pure HTTP client that POSTs to A. Adding a host needs
  **no A restart** (the bind + bearer already cover the tailnet).

---

## Enrolling a host

### Operator-manual prerequisites (outside the trust boundary)

These can't/shouldn't be automated — they establish trust roots:

1. Provision the box (Ubuntu 24.04, ideally same arch as A — aarch64).
2. **Tailscale:** `tailscale up` on the new box; ACL allows `A → new:22` and
   `new → A:9100`.
3. **Admin trust:** put your one-time provisioning key's *public* half in
   `new:~/.ssh/authorized_keys` (this is the `--admin-key`; the enroll script uses
   it for the full-shell setup, then the orchestrator key takes over for ops).
4. **Cloud firewall:** deny public inbound; tailnet-only.

### One command

```bash
soma-install enroll-host \
    --alias vps-b --tailnet-ip 100.102.145.110 \
    --ssh-user ubuntu \
    --identity ~/.ssh/soma-orchestrator \
    --admin-key ~/.ssh/id_ed25519 \
    [--ram-mb auto] [--max-concurrent 3] \
    [--degraded-webhook https://discord.com/api/webhooks/...]
```

(Equivalently `bash scripts/enroll_vps_host.sh …` — the script is standalone.)

What it provisions on the new host, **idempotently**: apt base, the repo (via
`git archive HEAD` — exact commit, no GitHub creds), a venv + editable install,
the `claude` CLI pinned to A's current version + the `claude-safe` wrapper, the
`somux` lead helper (symlinked into `~/.local/bin`; on a satellite it lists the
host's local leads and omits the channel row), the
forced-command guard `authorized_keys` line, scoped sudoers, `settings.json`
(`skipDangerousModePermissionPrompt`), the lean `lead-mcp-b.json`, a **secrets
subset**, and the **claude auth** (see Security). Finally it **self-verifies**
(guard probe + a throwaway spawn+LLM probe + a notify round-trip) and only then
marks the host `"status": "verified"` in `hosts.json`.

Re-running it on an already-enrolled host converges to the same state (safe).

### Placing a lead on a host

```
spawn_project(name="algo-trader", host="vps-b", tier="critical", ...)
resume_project(name="algo-trader", host="vps-b", tier="critical", session_uuid=...)
```

`tier="critical"` → 6000M/5000M cap; `"standard"` → 3000M/2500M (see
`config/claude/hosts.json` `tier_caps` and the guard's table — keep them in sync).

### Removing a host

```bash
soma-install remove-host --alias vps-b
```

Then move/stop any leads on it first, and revoke the orchestrator key from the
host's `authorized_keys`.

---

## Config surface — `config/claude/hosts.json`

```json
{
  "local": { "tailnet_ip": null, "ssh_user": null, "ssh_identity": null },
  "vps-b": {
    "tailnet_ip": "100.102.145.110",
    "ssh_user": "ubuntu",
    "ssh_identity": "/home/ubuntu/.ssh/soma-orchestrator",
    "max_concurrent": 3,
    "ram_mb": 11927, "headroom_mb": 1987,
    "tier_caps": { "critical": {"max_mb":6000,"high_mb":5000},
                   "standard": {"max_mb":3000,"high_mb":2500} },
    "status": "verified"
  }
}
```

Validate any time: `soma-install validate-hosts` (also run in `smoke_install.sh`).
Edits go through the atomic, schema-validated helper
(`mcp_servers/project_orchestrator/hosts.py`); don't hand-`sed` it.

> **Known duplication (v1):** tier caps live in both `hosts.json` (A-side admission)
> and the guard's `TIER_MAX/TIER_HIGH` bash table on each host. Enroll ships the
> guard with matching defaults; if you change caps, update both. Fast-follow: a
> single shipped `tier-caps.env` the guard reads at runtime.

---

## Security model

1. The orchestrator key on a host is **always** a forced command
   (`command="…/remote-exec-guard.sh",from="<A-tailnet>",restrict,…`) — never bare.
   A cannot run an arbitrary shell on B.
2. All orchestrator-path SSH uses `IdentitiesOnly=yes` + `BatchMode=yes`. The
   `--admin-key` is provisioning-only; retire it post-enroll for a 2-key model.
3. No listener ever binds `0.0.0.0` (only A runs one: `127.0.0.1` + A-tailnet).
4. Secrets on a host are an **INCLUDE allowlist** (`HERMES_NOTIFY_TOKEN`,
   `SOMA_RELAY_DOMAIN`, `HERMES_MAX_CONCURRENT_PROJECTS`, and `HERMES_DEGRADED_WEBHOOK`
   if opted in). Enroll runs a post-write **audit** that fails the enroll if any
   EXCLUDE-set key (`AUTH_*`, `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`,
   `DISCORD_BOT_TOKEN`, `HERMES_FILES_PASSWORD`, `HF_TOKEN`,
   `HERMES_ALLOWED_GITHUB_HANDLES`, `HERMES_NOTIFY_CHAT_ID`) leaked onto the host.
5. **Claude auth — prefer a durable long-lived token.** See "Cross-host auth" below.
   When falling back to copying `claudeAiOauth`, enroll copies *only* that key
   (never `mcpOAuth`) and asserts BOTH `accessToken` and `refreshToken` before
   shipping.
6. No secret **value** is ever printed or logged; every secret hop is a pipe and
   every check reports key-names + perms only.

---

## Cross-host auth (durable) — avoid the shared-OAuth 401

A and B must **not** share one interactive OAuth credential long-term. Claude Code's
interactive `claudeAiOauth` periodically **refreshes**, and that refresh rotates the
token at the IdP — which **invalidates the copy on the other host**. Symptom: a
remote lead authenticates fine, then hours later its pane shows
`Please run /login · API Error: 401` while A is unaffected. (Root-caused on
algo-trader@vps-b, 2026-06-18.)

**Durable fix — a long-lived token per remote host:**

```bash
claude setup-token          # one-time; prints a URL to authorize, returns a long-lived token
soma-install enroll-host --alias vps-b --tailnet-ip 100.… \
    --identity ~/.ssh/soma-orchestrator --admin-key ~/.ssh/id_ed25519 \
    --claude-oauth-token <token>        # or --claude-oauth-token @/path/to/tokenfile
```

Enroll writes it to the host's `secrets.env` as `CLAUDE_CODE_OAUTH_TOKEN` (0600,
loaded by the lead unit's `EnvironmentFile`). A long-lived token **takes precedence
over `~/.claude/.credentials.json` and does not refresh/rotate**, so A's refresh
cannot break it. `claude setup-token` requires an interactive authorize step
(operator action) — it does **not** disturb A's existing login.

**Fallback (no token):** enroll copies A's `claudeAiOauth` (asserting it has a
`refreshToken`). This restores a 401'd host immediately but is **unstable** — A's
next refresh can rotate it; re-enroll with `--claude-oauth-token` for a permanent
fix. Re-shipping A's fresh creds + restarting the lead (`resume_project host=…`) is
the stopgap to recover in the meantime.

## Out-of-cwd dependencies

A lead may import code outside its project cwd (e.g. algo-trader's `ws_shadow`
imports `~/finAgent`). Mirror such paths at enroll time so a remote lead doesn't hit
`FileNotFoundError`:

```bash
soma-install enroll-host … --extra-paths ~/finAgent,/opt/shared-lib
```

Each path is rsynced A→B preserving perms (idempotent). For a one-off after enroll,
rsync it directly with the admin key.

## Degraded mode — when A is unreachable

A is a single point of failure for a remote lead's notify/relay/revival. If a
**financial or otherwise crucial** lead runs on B, enroll with
`--degraded-webhook <discord-webhook>`. Then, **only on a connection-level failure**
to A's listener (refused/timeout = A down — *not* an HTTP error, which means A is
up), the lead's `notify_orchestrator` posts a minimal `[DEGRADED] …` line to that
outbound-only webhook. Default off; single-VPS and non-critical hosts are
unaffected.

---

## Operations cheat-sheet

| Task | Command |
|---|---|
| Add a host | `soma-install enroll-host --alias X --tailnet-ip 100.… --identity ~/.ssh/soma-orchestrator --admin-key ~/.ssh/id_ed25519` |
| Re-verify / converge a host | re-run the same `enroll-host` (idempotent) |
| Validate the registry | `soma-install validate-hosts` |
| Remove a host | `soma-install remove-host --alias X` |
| Place a lead | `spawn_project(host="X", tier="critical"\|"standard", …)` |
| Check a remote lead | guard verbs: `ssh -i ~/.ssh/soma-orchestrator user@ip 'has-session <name>'` / `capture` / `rc-url` |

See also the design reference under `docs/superpowers/plans/` and the runtime in
`mcp_servers/project_orchestrator/` (`spawner.py` RemoteRunner, `hosts.py`,
`registry.py` host/tier, `watchdog.py` tri-state).
