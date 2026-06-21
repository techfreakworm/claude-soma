# Multi-VPS Orchestration for Claude Soma — Design Plan

**Status:** PLANNING ONLY (2026-06-17). No code, no provisioning, no changes have been made. Awaiting operator approval before any implementation.
**Author:** soma-improver lead
**Method:** researched OpenClaw against primary sources; designed via deep sequential-thinking (14-step chain) grounded in the live `spawner.py` / registry / FI-NOTIFY / relay code.

---

## 0. TL;DR + recommendation

**Goal.** Keep the orchestrator on the current VPS ("VPS-A"), but let it spawn, command, monitor, and notify project-leads that physically run on a second VPS ("VPS-B", ~12 GB, same CPU). Move crucial-persistent leads (e.g. `algo-trader`) to VPS-B so they are physically isolated from the noisy lead swarm and can never be collateral victims of A's OOM-killer.

**Recommendation.** **Build a thin native extension, do not adopt OpenClaw.** Introduce a single new dimension — a per-lead **`host`** — plus a small **Runner** abstraction so each existing local primitive (`systemd-run`, `tmux`, `systemctl`, `capture-pane`, the registry, FI-NOTIFY, the relay) gains a **remote variant over an SSH-on-Tailscale control channel**. All orchestration *metadata* stays centralized on VPS-A; only *execution* is distributed to VPS-B. This rides the same "native rails" the project already rides (systemd + tmux + MCP), adds **zero new long-running services**, and is incrementally shippable with `host=local` as the backward-compatible default.

**Why not OpenClaw:** OpenClaw is a *single-machine* gateway runtime; adopting it (and its Claworc control-plane on Kubernetes) would mean replacing Claude Soma's Claude-Code-in-tmux lead runtime and discarding the existing spawner/registry/FI-NOTIFY/relay/dashboard. Disproportionate for a 2-VPS setup. **However, the OpenClaw ecosystem strongly validates every pillar of the design below** and is cited as the reference architecture (see §a).

---

## a. OpenClaw findings

The operator's spoken pointer (`openclaw.online`) **did not resolve** (connection refused) when fetched. The operator also flagged the STT was unclear ("PENCLAW"). Research therefore worked from primary sources — the GitHub repositories and `claworc.com` — and treated the broader web results with caution: several hits (`flowtivity.ai`, `remoteopenclaw.com`, `jacche.com`, assorted "I Switched from X to Y" Medium posts) read as low-quality SEO / AI-generated content and were **not** relied upon. Verified facts only below.

### What OpenClaw is
- **OpenClaw** (MIT-licensed; `github.com/openclaw/openclaw`; formerly *Clawdbot* / *Moltbot*) is **"a personal AI assistant you run on your own devices"**, connecting to messaging platforms (WhatsApp, Telegram, Slack, Discord). Its README describes a **"Local-first Gateway — single control plane for sessions, channels, tools, and events."**
- It runs as **one long-lived Node.js Gateway process** (daemon via launchd/systemd), single workspace at `~/.openclaw/workspace`, WebSocket API (`:18789`) for CLI/web/mobile-node pairing. It documents **"Remote access / Tailscale"** for reaching the gateway remotely.
- **It is NOT a native multi-host orchestrator.** It controls *local* agents on one machine; optional iOS/Android nodes pair *locally*. There is no built-in "command agents on other servers" capability.

### The orchestration layer (the part that's actually relevant)
- **Claworc** (`github.com/gluk-w/claworc`, `claworc.com`) is a separate **orchestrator for multiple OpenClaw instances** from one web dashboard. Key properties:
  - **Control-plane proxy:** "a single binary with 20 MB footprint that serves both the web dashboard and the proxy layer for instance access." **Instances are never directly exposed — all traffic routes through the control plane.**
  - **Virtual credentials:** "Agents connect to LLM providers through Claworc using virtual keys, so real API credentials never leave the control plane."
  - **Deployment:** **Docker** (single host) or **Kubernetes** (multi-node / production). Go + TypeScript.
  - The public docs **do not specify the transport protocol** between control plane and instances.
- Adjacent ecosystem projects worth knowing:
  - **`github.com/ClawHQ/openclaw-remote`** — a skill of "battle-tested workflows for managing OpenClaw agents **via SSH/tmux**" (provider config, security hardening, troubleshooting). This is the *de-facto remote-management pattern* in the ecosystem and **directly validates the SSH/tmux approach** proposed here.
  - **`github.com/Enderfga/claw-orchestrator`** — "Run **Claude Code**, Codex, Gemini, Cursor Agent and custom coding CLIs as one unified runtime… with first-class OpenClaw plugin support." This is the most *architecturally aligned* reference, since Claude Soma's leads are Claude Code sessions.
  - `claworc.com`, `ClawHQ/openclaw-remote`, `antoinersx/clawhost` (one-click cloud hosting), `awesome-openclaw` lists.

### Fit verdict
| Question | Answer |
|---|---|
| Does OpenClaw *itself* command agents across VPSs? | **No** — single-machine gateway. |
| Does the *ecosystem* solve multi-instance orchestration? | **Yes — Claworc** (proxy control plane, instances never exposed, virtual keys, Docker/K8s). |
| Can we drop it into Claude Soma? | **No** — it replaces the lead runtime (OpenClaw Gateway containers vs Claude-Code-in-tmux) and pulls in K8s; throws away existing investment. |
| Is it useful? | **Yes, as a reference.** Its proven patterns — proxy control plane, instances private-only, Tailscale substrate, SSH/tmux remote management, credentials that never leave the control plane — are adopted *natively* below. |

**Links:** OpenClaw `https://github.com/openclaw/openclaw` · Claworc `https://github.com/gluk-w/claworc` / `https://claworc.com` · openclaw-remote `https://github.com/ClawHQ/openclaw-remote` · claw-orchestrator `https://github.com/Enderfga/claw-orchestrator` · Milvus guide `https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md`.

---

## b. Architecture: orchestrator-on-A commanding leads-on-B

### b.1 The one core idea
Add a per-lead **`host`** dimension and a **Runner** abstraction. Today `spawner.py` builds argv lists and calls `subprocess.run([...])` locally. The remote case is the *same argv*, executed on B over SSH:

```
class Runner:           run(argv)  -> subprocess.run(argv)                       # host == local
class RemoteRunner:     run(argv)  -> subprocess.run(["ssh", target, "bash", "-s"], input=script(argv))
```

`script(argv)` ships a generated shell snippet over **stdin** (`ssh B bash -s`) rather than nesting quotes inside an `ssh B -- "..."` string — this sidesteps the double-escaping hazard of the `tmux … new-session … ';' pipe-pane …` command chain that `spawner.py` builds. The remote-argv/script builder gets unit tests.

Because the spawner already composes argv + `subprocess.run`, this is the **smallest possible diff** to a 612-line file: route each call through `Runner` selected by the lead's `host`.

### b.2 Control plane = SSH; substrate = Tailscale (WireGuard)
Transport options were weighed:

| Option | Verdict |
|---|---|
| **SSH command execution** (A runs the same `systemd-run`/`tmux`/`systemctl` argv on B) | **Chosen.** Near-trivial transform of existing argv; reuses 100% of the systemd-run+cgroup+tmux design on B; **no new daemon**; matches the ecosystem's `openclaw-remote` SSH/tmux pattern. |
| Custom agent/daemon on B (HTTP/gRPC) | Rejected for v1 — a whole new service to build/secure/version/watchdog; reinvents systemd+ssh. Documented as a future evolution if host-count grows. |
| Message bus (NATS/Redis/MQTT) | Rejected — heavyweight broker + SPOF; overkill for 2 hosts. |
| tmux-over-network / control-mode | Rejected — fragile, doesn't cover spawn/kill/systemd. |

**Substrate:** a **Tailscale (WireGuard) tailnet** giving each host a stable private IP; SSH (and the FI-NOTIFY back-channel + relay push) all ride *inside* the tunnel. This mirrors OpenClaw's own "Remote access / Tailscale" answer and means **no extra public ports are ever exposed**. Fallbacks if Tailscale is undesirable: hand-rolled WireGuard, or hardened public SSH with `ufw` allowlisting A's IP. Same-region host-to-host latency is sub-/low-ms — negligible for control ops (spawn/kill/peek are not hot loops).

### b.3 Per-primitive remoting (the whole surface)
| Primitive | Today (local) | Remote variant (host=vps-b) |
|---|---|---|
| spawn | `sudo -n systemd-run --collect --unit=claude-soma-lead-<n> -- tmux -L soma-lead-<n> new-session -d … claude-safe` | same argv via `ssh B bash -s` → unit + cgroup + tmux socket materialize **on B** |
| capture RC URL / peek (`somux`) | `tmux -L <sock> capture-pane -p` | ssh-wrapped; `somux` becomes host-aware (reads `host` from registry) |
| kill | `sudo -n systemctl stop <unit>` | ssh-wrapped to B |
| list / teammates | `tmux -L <sock> list-panes …` | ssh-wrapped; status reconciliation probes B |
| resume | `--resume <uuid>` spawn | identical, ssh-wrapped |
| context estimate | reads registry on A; transcript on disk | registry stays on A; transcript stat via ssh |

### b.4 Registry spanning two hosts
`registry.sqlite` stays **on VPS-A as the single source of truth** (orchestrator, dashboard, FI-NOTIFY drain, watchdog all read it). Add a **`host`** column to `projects` (default `'local'`). **No distributed DB, no SQLite-over-network, no sync** — B is stateless about orchestration metadata; it only runs units+tmux. Clean split: *metadata centralized on A, execution distributed to B*. Unit names (`claude-soma-lead-<n>`) and tmux sockets (`soma-lead-<n>`) are per-host namespaces and a lead lives on exactly one host, so no collisions; the `host` column tells every consumer which host to target.

### b.5 FI-NOTIFY spanning hosts
Today a lead's `hermes_notify` MCP POSTs to `127.0.0.1:9100` — the listener inside A's channel-spawned `hermes_api` process (durable SQLite event store → drain → Discord-primary DM, per the recent dual-route work). For a B-lead:
- Generalize the lead env from `HERMES_NOTIFY_PORT` to **`HERMES_NOTIFY_URL`**, injected at spawn = `http://<A-tailnet-ip>:9100/notify`.
- **Bind the listener to the tailnet interface** (currently `127.0.0.1` only), protected by a **Tailscale ACL** restricting `:9100` to {A,B} **plus a bearer token** validated by the listener.
- The durable store + drain + DM delivery stay centralized on A (consistent with §b.4). The recent `operator_dm.py` / `notify_lib.sh` dual-route (Discord primary) is unaffected — B-leads just feed events to A's listener, which DMs the operator.

### b.6 Relay spanning hosts
A B-lead that publishes an artifact should still surface **one** `files.<domain>` URL (preserving the relay-link HARD GATE). Make `soma-publish`/`soma-relay` **host-aware**: when run on B, `rsync` the file to A's relay dir (`soma@A:/var/lib/claude-soma/relay/…`) over the tailnet, then print the canonical `files.<domain>` URL served by A's existing Caddy. **No second public endpoint on B** — B has *no* public inbound at all. B needs only the relay-push SSH key, not Caddy/ACME/basicauth secrets.

---

## c. Placement: which leads go where

Declarative, operator-driven (no auto-scheduling in v1):
- Spawn API + registry gain **`host`** and a **`tier`**: `critical` (pin to B; per-cgroup memory reserved; aggressive auto-revive) vs `standard` (A; best-effort).
- **Default `host=local`** → existing single-VPS behavior is unchanged and fully backward-compatible.
- A small placement config (`config/claude/placement.json` or explicit spawn flags): e.g. `algo-trader → {host: vps-b, tier: critical}`.
- **VPS-B hosts few, well-behaved, long-lived critical leads**; VPS-A keeps the swarm of standard/ephemeral/experimental leads.
- **Bidirectional blast-radius reduction:** B-leads are protected from A's noisy leads, *and* A's leads are protected from a misbehaving critical lead.
- **Forward-compatible:** model `host` as a key into a small hosts table (host → tailnet addr → ssh identity), so the design generalizes to N hosts later without rework — but v1 ships exactly A+B.

---

## d. Memory isolation — how it stops cross-lead OOM kills

**The mechanism today.** The Linux **OOM-killer fires on global (host-level) memory pressure** and selects victims by `oom_score` across the *whole host*, regardless of cgroup boundaries. cgroups (which the project already uses via transient units) bound *accounting* and enable *clean teardown*, but a cgroup **without a hard `MemoryMax` does not stop the kernel from killing a sibling cgroup's process** when total RAM is exhausted. That is exactly today's collateral-kill path: lead X spikes → host RAM exhausted → kernel kills lead Y.

**Two complementary mitigations (defense in depth):**
1. **Physical isolation (the operator's ask).** Put `algo-trader` on **VPS-B**. A's OOM events *cannot reach* B's processes — different kernel, different RAM. Even if A melts down entirely, the crucial lead on B is untouched. Strongest possible guarantee.
2. **Per-lead hard caps within each host.** Add `--property=MemoryMax=` (hard cap) and `--property=MemoryHigh=` (soft throttle) to the `systemd-run` transient unit. A hard cap makes **the offender's own cgroup OOM first — killing only itself** — before global pressure builds. Optional `OOMScoreAdjust` biases the kernel toward the offender. This converts "noisy lead kills neighbors" into "noisy lead kills only itself," helps **on A immediately** (independent of multi-VPS), and on B protects critical leads from each other. Caveat: caps must be per-tier and conservative — a too-low cap can OOM a legitimate lead — so surface them in the placement config and log cgroup-OOM events to FI-NOTIFY (visible, not silent).

Net: physical separation for the crucial lead **plus** self-limiting spikes everywhere.

---

## e. Security of the control channel

A→B can drive `systemd-run` on B, so this is the highest-risk surface. Least-privilege, defense-in-depth:

- **Private substrate.** Tailscale tailnet; SSH bound to B's **tailnet interface only**. B's cloud security-group + `ufw` **deny all public inbound** (no 22/80/443 from the internet) — B is reachable only over the tailnet, outbound-only to LLM APIs. This removes most attack surface outright.
- **AuthN.** Dedicated `ed25519` keypair (`soma-orchestrator`) held on A only. B's `authorized_keys` pins it with `from="<A-tailnet-ip>"`, `restrict`, `no-agent-forwarding`, `no-pty` (as feasible), `BatchMode`.
- **AuthZ (least privilege).** SSH user on B = non-root `soma` with the **same narrowly-scoped sudoers** as A (`/etc/sudoers.d/99-claude-soma-spawner`): only `sudo -n systemd-run --unit=claude-soma-lead-* …` and `sudo -n systemctl stop/kill/reset-failed claude-soma-lead-*`. So even fully driving B, A can only manage `claude-soma-lead-*` units — **not arbitrary root.**
- **Forced-command guard (fast-follow).** `authorized_keys command="…/remote-exec-guard.sh"` that whitelists only the spawn/kill/capture verbs, capping blast radius if A's key is compromised. (v1 may start with restricted-ssh + sudoers scoping; add the guard immediately after.)
- **Notify back-channel.** `:9100` bound to the tailnet IP; Tailscale ACL limits it to {A,B}; bearer token validated by the listener.
- **Secrets minimization on B.** B holds only `CLAUDE_CODE_OAUTH_TOKEN`, the relay-push key, and the notify token — a strict **subset** of A's `secrets.env`. **No** dashboard/NextAuth/GitHub-OAuth/Caddy/Telegram/Discord secrets on B. Bounds the leak if B is breached.
- **Minimize B→A initiative.** Prefer A *pulls* from B; constrain the relay push key; the tailnet ACL must not let arbitrary B processes reach A's SSH (a compromised B-lead should be able to hit only the notify port).
- **Audit.** Log every remote exec (verb, lead, host, ts) to A's `activity.jsonl`.

---

## f. Phased implementation + risks

Each phase is independently shippable and reversible; `host=local` default keeps single-VPS behavior throughout. The operator's headline win (isolated `algo-trader`) lands at Phase 5, but the risky infra is de-risked first.

| Phase | Scope | Acceptance gate |
|---|---|---|
| **0 — Substrate** (no app code) | Provision VPS-B; Tailscale on A+B + ACLs; lock B's public firewall | `ssh soma@B` works over tailnet; B unreachable publicly |
| **1 — B runtime parity** | Bootstrap B as *lead-runtime-only* clone: repo, `.venv`, `claude-safe`, `bun`, scoped sudoers, OAuth token, **subset** secrets. No channel/dashboard/Caddy on B | Hand-run one `systemd-run`+`tmux`+`claude-safe` lead on B; it boots + reaches an LLM |
| **2 — Host-aware execution** | `Runner`/`RemoteRunner` in `spawner.py`; `host` column + spawn/kill/list/capture/resume routing; per-cgroup `MemoryMax`/`MemoryHigh` per tier | Orchestrator spawns/peeks/kills a test lead on B end-to-end; registry shows `host=vps-b` |
| **3 — Cross-host FI-NOTIFY** | `HERMES_NOTIFY_URL`; bind listener to tailnet; ACL + token | A B-lead's `notify_orchestrator` event reaches A's listener → operator Discord DM |
| **4 — Cross-host relay + watchdog** | host-aware `soma-publish` (rsync B→A); host-aware watchdog/lead-reaper liveness + revival for B-leads | B-lead publishes a file visible at `files.<domain>`; killing a B-lead triggers revival on B |
| **5 — Placement + migrate** | Declare `algo-trader` `tier=critical host=vps-b`; spawn; soak | `algo-trader` runs on B and **survives an induced OOM storm on A untouched** |

### Risks & mitigations
- **SSH quoting/escaping bugs** (tmux `;`-chain + claude argv) → use `ssh B bash -s` + generated script over stdin (not nested quotes); unit-test the builder.
- **Transient SSH/network failures on spawn/kill** → retries + idempotency; a blip must not orphan a lead or double-spawn.
- **B reboot / Tailscale down** → all B-leads unreachable; host-aware watchdog must *detect and alert* (FI-NOTIFY) rather than fail silently, then re-establish + revive when reachable.
- **Split-brain (registry vs reality on B)** → a reconciliation pass that ssh-probes B and corrects `status` (extend the existing local reconcile, host-aware).
- **OOM cap mis-tuning** → legit heavy lead hits `MemoryMax` and dies → per-tier conservative caps in config + log cgroup-OOM events to FI-NOTIFY (visible).
- **Control-channel compromise (A key stolen)** → blast radius bounded by scoped sudoers + forced-command guard + B's zero public exposure + secrets subset; rotate keys.
- **Ops cost** → a second VPS to patch/monitor; Tailscale dependency (WireGuard fallback documented).
- **Observability** → dashboard + `somux` must show `host` per lead so the operator always knows where something runs.

### Non-goals (v1)
Not building a generic N-tenant control plane; not running the channel/dashboard/Caddy on B; **not migrating to OpenClaw**; no auto-balancing/scheduling across hosts (placement is declarative, operator-driven).

---

## Appendix — concrete codebase touch-points (for when implementation is approved)
- `src/claude_soma/mcp_servers/project_orchestrator/spawner.py` — `Runner`/`RemoteRunner`; `_wrap_in_transient_unit` (+`MemoryMax`/`MemoryHigh`); host-routed spawn/kill/capture/list/resume.
- `…/project_orchestrator/registry.py` + `server.py` — `host`/`tier` columns + spawn API params + placement lookup.
- `…/project_orchestrator/watchdog.py` — host-aware `is_lead_alive` / revival (already extended for dual-route notify).
- `…/hermes_api/server.py` — `_start_notify_listener` bind address + bearer-token auth; `hermes_notify` server `HERMES_NOTIFY_URL`.
- `scripts/soma-relay` — host-aware publish (rsync B→A).
- `systemd/sudoers.d/99-claude-soma-spawner` — replicate on B; optional `remote-exec-guard.sh`.
- `scripts/bootstrap.sh` / `INSTALL.md` — a `--role=lead-runtime` profile for provisioning B (subset of services).
- New: `config/claude/placement.json`, `scripts/remote-exec-guard.sh`, hosts table.

**This document is a plan only. No code, configuration, provisioning, or service change has been performed. Implementation begins only on explicit operator approval, phase by phase.**
