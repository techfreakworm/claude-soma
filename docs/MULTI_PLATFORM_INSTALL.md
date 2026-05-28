# Multi-Platform Install — Design & Plan

Plan + design to make Claude Soma installable on any platform (today it is hard-wired to a
single Oracle Cloud Ubuntu ARM VPS).
Status: **Phase 1 implemented** (2026-05-28). Phases 2–4 remain design-only.

Phase 1 code lives in `src/claude_soma/platform/` and `src/claude_soma/install.py`.
Run `python -m claude_soma.install --dry-run` to see the full install plan without touching state.

This document records design and plan. It enumerates what is Linux/systemd/`apt`/ARM-specific
today, proposes abstractions, and phases the rollout.

---

## 1. Why this is hard today

Claude Soma currently assumes a very specific host. Concretely, the live tree hard-codes:

| Assumption | Where (evidence) |
|---|---|
| `apt` + Ubuntu 24.04 ARM | `scripts/vps_bootstrap.sh` (every `apt-get`, NodeSource deb, Caddy cloudsmith deb repo, ARM binaries for piper/whisper/playwright-chromium) |
| `systemd` for services | `systemd/*.service` + `*.timer`; `wizard/init.py` writes `/etc/systemd/system/*` and runs `systemctl` |
| `sudo systemd-run` + cgroups for lead isolation | `project_orchestrator/spawner.py` (`_wrap_in_transient_unit`, `SUDO_BIN`, `SYSTEMD_RUN_BIN`, `LEAD_UNIT_PREFIX`) — the core orchestration feature |
| `tmux` for PTYs (channel + every lead) | `spawner.py` (`TMUX_BIN`, per-lead `-L` socket), `claude-soma-channel.service`, `somux`, `healthcheck.sh` |
| Caddy as a system service | `Caddyfile`, `wizard/init.py` (`render_caddyfile`, `systemctl reload caddy`), `vps_bootstrap.sh` |
| FHS paths `/opt/claude-soma`, `/etc/claude-soma`, `/var/log/claude-soma` | `.mcp.json`, all units, `spawner.py`, `wizard/init.py`, `CLAUDE.md` (canonical paths) |
| A literal `ubuntu` user + `/home/ubuntu` | units (`User=ubuntu`), `spawner.py` (`LEAD_USER`, `LEAD_HOME`), bootstrap (`usermod -aG docker ubuntu`) |
| `HERMES_*` env vars baked with absolute Linux paths | `.mcp.json`, units, `spawner.py` defaults |
| bash scripts | `scripts/*.sh` (bootstrap, deploy, healthcheck, channel-claude, claude-safe, somux) |
| iptables / Oracle ingress quirk | `vps_bootstrap.sh` step 2 (insert ACCEPT before Oracle's REJECT) |

Most are already **env-overridable** (`HERMES_CLAUDE_BIN`, `HERMES_TMUX_BIN`,
`HERMES_SUDO_BIN`, `HERMES_SYSTEMD_RUN_BIN`, `HERMES_LEAD_*`, `HERMES_LEAD_LOG_DIR`, …), which
is the seam we build on — but the *defaults* and the *service/package layers* are Linux-only.

---

## 2. Target matrix

First-class = full feature parity, supported path. Degraded = works with documented caveats.
N/A = not offered on that platform.

| Platform | Channel/bot | Voice (whisper/piper) | **Lead isolation** | Service mgr | Reverse proxy | Tier |
|---|---|---|---|---|---|---|
| **Linux + systemd** (Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE) | first-class | first-class | **cgroup-isolated** (`systemd-run`) | systemd | Caddy (system) | **first-class** |
| **Alpine / no-systemd Linux** | first-class | first-class | degraded (no `systemd-run`; OpenRC/`setsid`+tmux, no cgroup) | OpenRC / supervisord | Caddy | degraded |
| **macOS** (Apple Silicon + Intel) | first-class | first-class (brew whisper/piper, or build) | degraded (no cgroups; launchd-managed tmux sessions, no kill-isolation) | launchd | Caddy (brew) | degraded |
| **Windows + WSL2** | first-class | first-class | **cgroup-isolated** (WSL2 ships systemd) | systemd (in WSL2) | Caddy | **first-class (via WSL2)** |
| **Windows native** | best-effort | best-effort (whisper/piper Windows builds) | none (Windows Service per lead, no cgroup) | Windows Service / NSSM | Caddy (Windows) | best-effort |
| **Container / Compose** (any host with Docker/Podman) | first-class | first-class (baked image) | nuanced (cgroup-in-container — see §7) | container restart policy | Caddy sidecar / host | portability shortcut |

Headline rule to document loudly: **cgroup-isolated leads require Linux + systemd.** Everywhere
else, leads still run (tmux/Windows-Service/process), but a channel restart's blast-radius
protection is weaker or absent. This is the single biggest feature-parity cliff and must be
stated up front so users pick WSL2 (not native Windows) when they want parity.

### Per-feature degradation detail

- **Lead isolation** is the crown jewel and the least portable. On non-systemd hosts the
  fallback is: spawn the lead's tmux server under `setsid`/its own process group (or a
  per-lead Windows Service), accept that a channel crash *could* take leads with it, and lean
  harder on the existing **liveness reconciliation + killed-lead resume** (FUTURE_IMPROVEMENTS)
  to recover. Document that leads are "best-effort durable" off Linux.
- **Voice** is portable in principle (whisper.cpp + piper both build/ship on macOS & Windows),
  but the *binaries and model paths* are the work: ARM-vs-x86, brew formula vs build, Windows
  release archives.
- **Reverse proxy / TLS** is the most portable (Caddy is cross-platform); the only host-specific
  bit is firewall/ingress (the Oracle iptables quirk is OCI-only).

---

## 3. Install entrypoint & method

**Recommendation: a Python bootstrapper, `python -m claude_soma.install`,** with thin
OS-native shims (`install.sh` for POSIX, `install.ps1` for Windows) that only bootstrap
Python 3.12 and then hand off. Rationale: Python 3.12 is already a hard dependency; a Python
installer gives us real cross-platform logic (OS/arch detection, templating, JSON/TOML) without
maintaining parallel large bash + PowerShell programs. The existing `soma-init` wizard
(`src/claude_soma/wizard/init.py`) is the seed — it already prompts, validates a domain,
writes secrets, and renders a Caddyfile + systemd units; it just needs to become
platform-aware and absorb dependency + service installation.

```
install.sh   (POSIX shim)  ─┐
install.ps1  (Windows shim) ─┼─►  python -m claude_soma.install   ──►  reuses/extends
soma-init    (existing)     ─┘        (the cross-platform bootstrapper)        wizard.init
```

What the bootstrapper does, in order:

1. **Detect** OS / distro / arch / init system / container-ness.
   - OS: `platform.system()` (`Linux`/`Darwin`/`Windows`).
   - distro: `/etc/os-release` `ID`/`ID_LIKE`.
   - arch: `platform.machine()` (`aarch64`/`arm64` vs `x86_64`/`AMD64`).
   - init: presence of `systemctl` + `systemd` as PID 1 vs launchd vs WSL2 (detect via
     `/proc/sys/kernel/osrelease` containing `microsoft`) vs Windows.
2. **Resolve the package-manager adapter** (apt/dnf/pacman/zypper/apk/brew/winget|choco) and
   install runtime deps: python3.12, node>=22 (note: bootstrap currently installs Node **20** —
   bump and pin), ffmpeg, tmux (POSIX only), Caddy, the **claude CLI** (native install for
   `--channels`), whisper.cpp (build or binary), piper (binary) + a voice model, bun (for the
   telegram plugin's MCP server), and optional extras (gh, docker, ngrok, playwright + chromium).
3. **Resolve the service-manager adapter** and install/enable units (systemd ↔ launchd ↔
   Windows Service/NSSM ↔ WSL2-systemd).
4. **Resolve the paths layer** (where code/secrets/logs/state live) and create dirs with
   correct perms/owner.
5. **Place secrets** in the per-OS secret location.
6. **Optionally** set up the reverse proxy + domain (Caddy) — skippable for a local/headless
   install (then the dashboard binds localhost only).
7. Run the existing post-steps (`claude auth login`, telegram pairing prompts, default-routine
   backfill).

`--dry-run` (print the plan), `--non-interactive` (env-driven for CI/headless), and
`--features=...` (e.g. skip voice or social) are table stakes.

---

## 4. The four adapters (concrete refactors)

The whole effort reduces to four abstraction layers plus templated unit/plist generation.
Each is small because the env-var seams already exist.

### 4.1 Paths / config layer — `claude_soma.platform.paths`

Today paths are string constants scattered across `.mcp.json`, units, `spawner.py`,
`wizard/init.py`. Centralize into one module that resolves per-OS, honoring overrides
(`HERMES_*` and a new `SOMA_HOME`):

| Logical path | Linux (FHS) | macOS | Windows |
|---|---|---|---|
| code root | `/opt/claude-soma` | `/opt/claude-soma` or `/usr/local/...` | `%PROGRAMFILES%\ClaudeSoma` |
| config/secrets | `/etc/claude-soma/secrets.env` | `~/Library/Application Support/ClaudeSoma/secrets.env` | `%PROGRAMDATA%\ClaudeSoma\secrets.env` |
| logs | `/var/log/claude-soma` | `~/Library/Logs/ClaudeSoma` | `%PROGRAMDATA%\ClaudeSoma\logs` |
| per-user state (activity.jsonl, registry, pw store) | `~/.claude-soma`, `/opt/.../registry.sqlite`, `~/.claude-pw` | `~/Library/Application Support/ClaudeSoma/...` | `%APPDATA%\ClaudeSoma\...` |
| projects/work dir | `/home/ubuntu/projects` | `~/projects` | `%USERPROFILE%\projects` |

Notes:
- Prefer **XDG** on Linux for a *user-mode* install (`$XDG_CONFIG_HOME`, `$XDG_STATE_HOME`,
  `$XDG_DATA_HOME`) while keeping `/opt` + `/etc` for the *system-mode* (root) install. Offer
  both modes.
- `.mcp.json` is currently static with absolute `/opt/...` paths and `HERMES_*` envs. It must
  become **templated at install time** from the paths layer (the wizard writes the resolved
  `.mcp.json` rather than shipping a Linux-only one). Keep `HERMES_*` *names* (interface
  stability per `CLAUDE.md`) but fill their *values* from the paths layer.
- Resolves the existing `claude.mayankgupta.in` vs `soma.mayankgupta.in` drift
  (`api/main.py` default vs Caddyfile/units) by making the domain a single config value.

### 4.2 Package-manager adapter — `claude_soma.platform.pkg`

A small dispatch table mapping a logical package to per-manager install commands:

| Manager | Detect | Example (`ffmpeg`) |
|---|---|---|
| apt | `apt-get` + Debian/Ubuntu `ID_LIKE` | `apt-get install -y ffmpeg` |
| dnf | `dnf` + Fedora/RHEL | `dnf install -y ffmpeg` (may need RPM Fusion) |
| pacman | `pacman` + Arch | `pacman -S --noconfirm ffmpeg` |
| zypper | `zypper` + openSUSE | `zypper -n install ffmpeg` |
| apk | `apk` + Alpine | `apk add ffmpeg` |
| brew | `brew` + Darwin | `brew install ffmpeg` |
| winget/choco | Windows | `winget install ...` / `choco install -y ffmpeg` |

Hard cases that need per-OS recipes, not just a name map:
- **Caddy**: apt uses the cloudsmith deb repo (current code); other managers have native Caddy
  packages or a static binary download. Make "install Caddy" a recipe, not a package name.
- **claude CLI**: `npm i -g @anthropic-ai/claude-code` then `claude install latest` (native
  binary) — same on all OSes that have node; the *PATH* of the native binary differs.
- **whisper.cpp**: build from source (cmake/clang) on Linux/macOS; on Windows prefer a prebuilt
  release or WSL2. Model download (`base.en` per current config — see KNOWN_BUGS #6) is
  OS-agnostic curl/Invoke-WebRequest.
- **piper**: per-OS/arch release archive (the code hard-codes `piper_linux_aarch64.tar.gz`).
- **bun**: `bun.sh/install` (POSIX) vs `powershell -c "irm bun.sh/install.ps1 | iex"` (Windows).
- **playwright + chromium**: `playwright install chromium` is cross-platform; the
  `--executable-path` symlink trick (`/usr/local/bin/playwright-chromium`) is Linux-ARM-specific
  and should become an OS-resolved path.

### 4.3 Service-manager adapter — `claude_soma.platform.services`

Abstracts "install a long-running service" and "install a timer/scheduled job" behind one
interface with four backends. Each backend renders from a **single logical service description**
(name, exec argv, env, working dir, restart policy, log paths).

| Backend | Service unit | Timer/cron | Notes |
|---|---|---|---|
| systemd (Linux, WSL2) | `.service` (current units) | `.timer` (current) | reuse `wizard.init.render_systemd_unit`; the channel's `Type=oneshot`+`RemainAfterExit` + tmux pattern stays |
| launchd (macOS) | `~/Library/LaunchAgents/*.plist` or `/Library/LaunchDaemons` | `StartCalendarInterval` in the plist | `KeepAlive` ≈ `Restart=always`; no cgroup |
| Windows Service / NSSM | NSSM-wrapped service per role | Task Scheduler (`schtasks`) | NSSM gives restart-on-failure for non-service exes |
| WSL2 | same as systemd | same | WSL2 ships systemd; this is the parity path |

The **lead spawner** is the special case (see §5) — it is not a static service but a per-lead
*transient* one, so it gets its own adapter method `spawn_isolated(name, argv, env)` with
per-backend isolation strength.

### 4.4 Reverse-proxy / firewall layer

- Caddy config generation already lives in `wizard.init.render_caddyfile` — keep it; only the
  *install + reload* command is per-OS (`systemctl reload caddy` ↔ `brew services restart caddy`
  ↔ Windows service restart).
- Firewall/ingress is host-specific and mostly out of scope: the **OCI iptables-before-REJECT**
  quirk is Oracle-only and should live behind an explicit `--cloud=oci` flag, not in the generic
  path (also tracked as a bootstrap fix in FUTURE_IMPROVEMENTS / CHECKLIST #38).

---

## 5. The lead-spawner abstraction (the crux)

`spawn_background_lead` in `spawner.py` is the least portable code. Today it builds:

```
sudo -n systemd-run --collect --quiet --unit=claude-soma-lead-<name>.service \
  --property=Type=oneshot --property=RemainAfterExit=yes --property=User=ubuntu ... \
  -- /usr/bin/tmux -L soma-lead-<name> new-session -d -s soma-proj-<name> -c <cwd> <claude...>
```

Propose an **isolation-strategy interface** with a per-platform implementation; the rest of the
spawner (name validation, cwd pre-trust, RC-URL capture, per-lead logging, liveness) is already
platform-neutral and stays.

| Strategy | Platform | Isolation strength | Mechanism |
|---|---|---|---|
| `SystemdRunStrategy` (current) | Linux + systemd, WSL2 | **strong** (sibling cgroup; channel restart can't reach it) | `sudo systemd-run` + dedicated `tmux -L` socket |
| `OpenRcStrategy` / `SetsidStrategy` | Alpine / no-systemd Linux | medium | `setsid` new process group + dedicated `tmux -L` socket; no cgroup |
| `LaunchdStrategy` | macOS | medium | per-lead LaunchAgent **or** detached tmux under its own session; no cgroup |
| `WindowsServiceStrategy` | Windows native | weak | NSSM service per lead wrapping the claude process (Windows has no tmux — see open question on PTY) |

Key portability facts to preserve from the notes:
- **claude needs a real PTY** or it drops to `--print` mode (cgroup-teardown note). tmux
  provides it on POSIX. **Windows has no tmux** — this is the hardest native-Windows problem;
  candidates are ConPTY via a helper, or simply declaring native Windows out of parity and
  steering Windows users to WSL2 (where tmux + systemd both work).
- **Liveness = `tmux has-session`, not the service state** (liveness-reconciliation note). On
  Windows-Service the liveness signal changes to "is the wrapped process alive," which the
  reconciler must learn (another reason WSL2 is the clean path).
- Secrets must **not** appear on the command line (cgroup-teardown note rejected
  `systemd-run --scope` for exactly this). Each backend must load the token from a file/env,
  never argv.

---

## 6. Phased rollout

| Phase | Scope | Deliverables | Risk |
|---|---|---|---|
| **Phase 1 — Any Linux** | Generalize package + service managers; **keep systemd**. Distro-agnostic bootstrap (apt/dnf/pacman/zypper/apk), XDG vs `/opt` modes, templated `.mcp.json` + units, paths layer. Lead isolation unchanged (systemd-run). | `claude_soma.platform.{paths,pkg,services}`; `python -m claude_soma.install` for Linux; bootstrap fixes (Node 22, whisper `base.en`, iptables behind `--cloud=oci`). | Low — same isolation model; mostly packaging. |
| **Phase 2 — macOS** | launchd backend, brew packages, degraded isolation (no cgroups). | launchd plist templating; brew recipes; `LaunchdStrategy`/`SetsidStrategy`; macOS paths (`~/Library/...`). | Medium — voice binaries (whisper/piper) on Darwin; no cgroup parity (document it). |
| **Phase 3 — Windows via WSL2** | Full parity by running the Linux install **inside WSL2** (systemd + tmux + cgroups all present). A thin `install.ps1` provisions WSL2 + an Ubuntu distro, then runs the Phase-1 Linux path. | `install.ps1` WSL2 provisioner; docs; reuse Phase 1 unchanged. | Medium — WSL2 setup friction, networking/port-forwarding for the dashboard, autostart of the WSL distro at boot. |
| **Phase 4 — Native Windows** | Best-effort: Windows Service/NSSM + Task Scheduler, no cgroups, ConPTY for the lead PTY (or no-team mode). | `WindowsServiceStrategy`, winget/choco recipes, ConPTY helper or "leads are best-effort" caveat. | High — no tmux, no cgroups, PTY story unclear; lowest ROI. |

### Phase 1 — implemented (round 2)

Modules shipped:

| Module | Purpose |
|---|---|
| `src/claude_soma/platform/_action.py` | `Action` dataclass — the plan unit |
| `src/claude_soma/platform/paths.py` | `Paths` frozen dataclass; `resolve()` for system/user mode; `render_mcp_json()` |
| `src/claude_soma/platform/pkg.py` | `PackageManager` enum; `detect_package_manager()`; `LOGICAL_PACKAGES`; `pkg_install()` |
| `src/claude_soma/platform/services.py` | `ServiceBackend` ABC with `isolation_strength`; `SystemdBackend`; stub backends for Phases 2–4 |
| `src/claude_soma/install.py` | `python -m claude_soma.install --dry-run|--apply` entrypoint; `build_plan()`; `_execute_action()` |

Round-2 constraints met:
- `--dry-run` prints every privileged command and every file write; makes zero state changes; exits 0.
- `--apply` flag required for actual execution; no implicit default.
- Secrets read from file/env only; never passed as argv (audited — see sudo audit below).
- No new dependencies; stdlib only.
- Node 22 via NodeSource `setup_22.x`.
- whisper model `ggml-base.en.bin`.
- OCI iptables ACCEPT rule gated behind `--cloud=oci` flag.
- `HERMES_*` env names preserved as interface contracts; paths layer fills values only.
- `ServiceBackend.isolation_strength` property on every backend.
- 274 tests pass (161 new platform tests + 113 pre-existing).

### Alternative / parallel track — Container or Compose (portability shortcut)

A `Dockerfile` + `docker-compose.yml` (or Podman) is the fastest way to "runs anywhere with a
container runtime," and is worth doing *alongside* Phase 1 as the recommended path for users who
don't want a native install.

Tradeoffs to document:
- **cgroup-in-container nuance**: the lead-isolation feature relies on `systemd-run` creating
  *sibling* cgroups. Inside a container you need either (a) systemd as PID 1 in the container
  (`systemd`-in-Docker, privileged-ish, cgroup v2 delegation) so `systemd-run` works, or (b) drop
  to the degraded `setsid`+tmux strategy inside the container. Plain containers don't give you
  per-lead cgroup isolation for free.
- **Voice packaging**: bake whisper.cpp + piper + the `base.en` model into the image (large image,
  but reproducible) vs mount them as a volume.
- **Secrets**: mount `secrets.env` as a file/secret, not baked into the image.
- **Dashboard/TLS**: run Caddy as a sidecar or terminate TLS on the host; map ports.
- **Pro**: one artifact, trivial upgrades, host-OS-agnostic. **Con**: the headline cgroup feature
  is the exact thing containers complicate; you trade native isolation for portability.

---

## 7. Risks & open questions

1. **PTY on native Windows.** claude needs a real PTY (drops to `--print` otherwise) and there
   is no tmux. ConPTY is the likely answer but unproven for this use; the pragmatic call may be
   "native Windows = WSL2 only for the orchestrator." *Decision needed before Phase 4.*
2. **Isolation parity expectations.** Off Linux+systemd there is no cgroup blast-radius
   protection. Is "degraded isolation" acceptable for the macOS/Alpine tiers, or do we gate the
   orchestrator feature to first-class platforms only? *Product decision.*
3. **Scope: operators vs contributors.** Is the goal self-hosting anywhere (full stack) or also
   letting contributors run a degraded local instance + the test suite on macOS/Windows? The test
   suite already self-skips voice tests without whisper/piper, so a contributor path is closer
   than a full operator path.
4. **`HERMES_*` env naming.** `CLAUDE.md` freezes the names for interface stability. Keep names,
   template values — but a cross-platform install is a natural moment to *consider* a coordinated
   rename to `SOMA_*` (out of scope here; flag it).
5. **Node version drift.** Bootstrap installs Node **20**; the brief/target is **node>=22**.
   Reconcile during Phase 1 (also surfaces in FUTURE_IMPROVEMENTS).
6. **whisper model mismatch** (KNOWN_BUGS #6): the installer must download the model that
   `.mcp.json` actually references (`base.en`), not the legacy `large-v3-turbo`.
7. **Per-lead log rotation** is unsolved even on Linux (FUTURE_IMPROVEMENTS / KNOWN_BUGS #5);
   each OS's log location in the paths layer should ship with a rotation story.

---

## 8. Recommended first step

**Build the paths layer + package-manager adapter and ship Phase 1 (any-Linux) only**, because:
- It is the highest-leverage, lowest-risk slice (keeps the proven systemd isolation untouched).
- It forces the `.mcp.json`/unit *templating* refactor that every later phase depends on.
- It immediately widens the audience from "OCI Ubuntu ARM" to "any Linux distro," which is most
  realistic self-host targets, before taking on the harder macOS/Windows PTY+isolation problems.

Concretely: extract `claude_soma.platform.paths` from the scattered constants, make
`soma-init`/`python -m claude_soma.install` consume it, add the pkg adapter for
apt/dnf/pacman/zypper/apk, and template `.mcp.json` + the systemd units from the paths layer.
Land the Phase-1 bootstrap fixes (Node 22, whisper `base.en`, OCI-only iptables behind a flag)
in the same pass.

---

## Sudo audit (Phase 1)

Every privileged command issued by `python -m claude_soma.install --apply` passes through
`_execute_action()` in `src/claude_soma/install.py`. Each Action is created by `build_plan()`.
No secret values are ever placed on a subprocess argv — tokens and credentials are written to
`secrets.env` (mode 600) and read at runtime by systemd via `EnvironmentFile=`.

Privileged actions in the Phase 1 plan (in order):

| # | Action description | Command pattern | Why sudo |
|---|---|---|---|
| 1 | Create system directories | `sudo mkdir -p <dirs>` | FHS dirs under `/opt`, `/etc`, `/var/log` require root |
| 2 | Set directory ownership | `sudo chown -R <user>:<user> <dirs>` | Directories created as root; ownership handed to install user |
| 3–N | Install core packages (ffmpeg, tmux, curl, git, python3.12, build-essential, openssl, caddy, node22, gh) | `sudo apt-get install -y ...` (or dnf/pacman/zypper/apk equivalent) | System package installation |
| N+1 | Install whisper build deps | `sudo apt-get install -y cmake clang libopenblas-dev` | System packages |
| N+2 | Install playwright-mcp | `sudo apt-get install -y ...` | System packages |
| N+3 | Write systemd unit files | `sudo tee /etc/systemd/system/<name>.service` | `/etc/systemd/system` is root-owned |
| N+4 | Write systemd timer files | `sudo tee /etc/systemd/system/<name>.timer` | Same |
| N+5 | Reload systemd daemon | `sudo systemctl daemon-reload` | Requires root |
| N+6 | Enable and start services | `sudo systemctl enable --now <names>` | Requires root |
| N+7 | OCI iptables rule (--cloud=oci only) | `sudo iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT` | iptables requires root |

Non-privileged actions (no sudo):

- Clone/update repo via `git` (runs as install user)
- Create Python venv (`python3.12 -m venv`)
- Install pip packages inside venv (`pip install -e .`)
- Install claude CLI (`npm install -g` or native install — runs as install user)
- Build whisper.cpp (`cmake`/`make` in user-writable temp dir)
- Download piper binary + voice model (curl to user-writable path)
- Install playwright chromium (`playwright install chromium` — user-level)
- Write secrets template file (written to `secrets_env` path, chown'd to user)
- Write `.mcp.json` (written to `~/.mcp.json`, user-owned)
- Register default routines in registry DB (Python, user-writable DB)
- Install bun via `curl bun.sh/install | bash` (user-level, installs to `~/.bun`)
