# INSTALL.md — claude-soma fresh-VPS install runbook

This is the canonical install runbook for bringing claude-soma up on a fresh Ubuntu 24.04 VPS.

For a quickstart, see README.md. For an architecture overview, see
`docs/superpowers/specs/2026-05-22-hermes-claude-design.md`.

---

## Two external prerequisites (outside the scripts)

These two cannot be configured by the bootstrap script — they live outside
the VPS. The script prints reminders at the end, but you must action them
manually:

1. **DNS A records** at your DNS provider for `soma.<your-domain>` and
   `files.<your-domain>` pointing at your VPS public IP.

2. **Cloud-provider firewall** (OCI Security List, AWS Security Group,
   GCP VPC firewall, DigitalOcean Firewall, etc.) — inbound TCP ports 80
   and 443 from `0.0.0.0/0`. This is INDEPENDENT of the on-box ufw rules
   the bootstrap configures.

Without both, Caddy cannot obtain TLS certificates from Let's Encrypt
(the ACME challenge requires inbound port 80), and your sites will be
unreachable even though every local service is running healthy.

The bootstrap prints a copy-pasteable block with provider-specific
cloud-provider firewall instructions at the end (via `show-dns-setup.sh`).
You can re-print it anytime:

    bash scripts/show-dns-setup.sh

---

## A note on errors

The bootstrap script (`scripts/bootstrap.sh`) and `scripts/finalize-caddy.sh` are designed to
NEVER crash with a raw error thrown in your face. Every step that can fail for a known or
expected reason — domain not set yet, DNS not pointing, secrets not filled, log directory
missing, Caddy not started yet — detects the condition and prints a clear yellow (warning) or
red (fatal) box explaining what happened and the exact commands to fix it. The script then
either continues with the rest of the install or exits cleanly with next-step instructions.

In particular:

- Step 13 (Caddy install): Caddy **not** serving your sites at bootstrap time is completely
  expected — it needs your domain and DNS records first. You will see a yellow "Caddy is
  installed but not yet serving your sites" friendly box. This is NOT an error. Follow the
  instructions in the box to set `SOMA_DOMAIN` in secrets.env and run `finalize-caddy.sh`
  after DNS propagates.

- `finalize-caddy.sh`: Detects whether `caddy.service` is already active (reload path) or
  not yet started (enable --now path), and handles both without raw systemd errors.

If you see a raw error or stack trace that is NOT wrapped in a friendly box, that is a bug —
please report it at https://github.com/techfreakworm/claude-soma/issues.

---

## Prerequisites

External accounts you must provision before starting. Have these values ready in a notes file:

1. **Claude Max OAuth** — sign up at claude.ai; run `claude` once interactively on any browser-capable machine to mint `CLAUDE_CODE_OAUTH_TOKEN`. Copy the `oat-...` token.
2. **GitHub OAuth App** — create at `https://github.com/settings/developers`. Set callback URL to `https://soma.<your-domain>/api/auth/callback/github`. Save `AUTH_GITHUB_ID` + `AUTH_GITHUB_SECRET` (NextAuth v5 names — note: NOT _CLIENT_ID / _CLIENT_SECRET).
3. **Telegram Bot** — DM `@BotFather`, create a bot, save `TELEGRAM_BOT_TOKEN`. Get your personal chat ID: DM your bot, then call `https://api.telegram.org/bot<token>/getUpdates` and find `chat.id` — that is `HERMES_NOTIFY_CHAT_ID`.
4. **xAI account** — for the `grok` CLI (used for image generation). Sign up at x.ai.
5. **ChatGPT subscription** — for the `codex` CLI. A Plus or higher subscription is required.
6. **Hugging Face account** (free tier sufficient) — for the `hf` CLI.
7. **Domain** — DNS A-records for `soma.<your-domain>` and `files.<your-domain>` pointing at the VPS public IP. TTL 300 recommended.

---

## Step 1 — Provision the VPS

- Ubuntu 24.04, 2+ GB RAM, 30+ GB disk, public IPv4.
- SSH in as `ubuntu` (or as root, then create user `ubuntu`).
- Open inbound ports 80 and 443 in your firewall / security-group rules.
- **Oracle Cloud (OCI) only:** the bootstrap applies an iptables fix that makes Caddy publicly reachable. You must pass `--cloud=oci` (see Step 3). Without it, ports 80/443 appear open locally but Caddy is unreachable from the public internet.

---

## Step 2 — Clone the repo

```bash
sudo mkdir -p /opt/claude-soma
sudo chown ubuntu:ubuntu /opt/claude-soma
cd /opt/claude-soma
git clone https://github.com/techfreakworm/claude-soma.git .
```

---

## Step 3 — Run the bootstrap

```bash
cd /opt/claude-soma
sudo bash scripts/bootstrap.sh           # generic Ubuntu 24.04
sudo bash scripts/bootstrap.sh --cloud=oci  # on Oracle Cloud Free Tier
```

What it does (see `scripts/bootstrap.sh` for the full source):

- apt-installs system dependencies: build-essential, git, curl, wget, pkg-config, python3.12, python3.12-venv, python3.12-dev, ffmpeg, cmake, clang, tmux, jq, sqlite3, libsox-dev, libssl-dev, Node 22, Caddy
- npm-installs globally: `@anthropic-ai/claude-code` (the `claude` CLI), `markserv`, `playwright-mcp`
- Builds whisper.cpp (ARM) and downloads the `ggml-base.en.bin` STT model
- Installs piper ARM binary + `en_US-ryan-medium.onnx` voice
- Creates Python venv at `.venv` and runs `pip install -e .` (includes `huggingface_hub[cli]`)
- Builds the dashboard: runs `scripts/build_frontend.sh` (pnpm install + build + copies `.next/static` and `public` next to `standalone/server.js`)
- Creates runtime directories: `/var/lib/claude-soma/{relay,staging,engagement}`, `/etc/claude-soma`, `/var/log/claude-soma`
- Seeds `/var/lib/claude-soma/engagement/queue.jsonl`
- Installs all `systemd/claude-soma-*.{service,timer}` unit files and runs `systemctl daemon-reload`
- Enables and starts the four long-running services: `claude-soma-channel`, `claude-soma-api`, `claude-soma-frontend`, `claude-soma-markserv`
- Enables all timers: healthcheck, cache-refresh, secrets-backup, pw-refresh, usage-snapshot, rc-url-refresh, idle-reaper, daily-status, listener-healthcheck, engagement-drip, channel-clear, relay-cleanup
- Installs `scripts/claude-safe.sh` to `/usr/local/bin/claude-safe` (the wrapper that strips the Telegram plugin before invoking claude in lead sessions — prevents leads from hijacking the channel)
- Installs `bun` runtime via `curl https://bun.sh/install | bash` for the `ubuntu` user; symlinks `~/.bun/bin/bun` to `/usr/local/bin/bun` (required by the Telegram plugin's MCP server, which runs `bun server.ts` as a child of `claude --channels`)
- Installs operator CLI helpers to `/usr/local/bin/`: `somux` (list/attach/peek project-lead tmux sessions), `soma-relay` (publish files to the Caddy file relay), `soma-publish` (alias for `soma-relay publish`)
- Installs `/etc/sudoers.d/99-claude-soma-spawner` — the sudoers grant that lets the `ubuntu` user run `sudo -n systemd-run` and `sudo -n systemctl stop/reset-failed/kill claude-soma-lead-*` without a password (required for multi-agent lead orchestration — see "Notes on special components" below). The source file is validated with `visudo -cf` before install; the installed file is validated after. A syntax error aborts with a friendly error; the file is never left in a broken state.
- Installs the base Caddyfile to `/etc/caddy/Caddyfile` (with `import /etc/caddy/conf.d/*.caddyfile` at the bottom); the site-specific configs (`soma.<domain>`, `files.<domain>`) are rendered later by `finalize-caddy.sh` after secrets are set
- (OCI only) Applies the iptables ACCEPT rules for ports 80/443 before Oracle's default REJECT rule, then saves with `netfilter-persistent`

---

### Step 3b — Add DNS records

The bootstrap ends by printing the DNS A records you must add at your DNS provider:

```
==============================================================
DNS SETUP REQUIRED
==============================================================

  Type    Name (Host)             Value (points to)
  ----    ----------------------  ------------------
  A       soma.<your-domain>      <YOUR_VPS_IPV4>
  A       files.<your-domain>     <YOUR_VPS_IPV4>
  ...
```

Add these records at your domain registrar (Cloudflare, Namecheap, GoDaddy, etc.). Allow 1-5 minutes for DNS propagation.

Re-run the instructions anytime:

```bash
bash scripts/show-dns-setup.sh           # print records
bash scripts/show-dns-setup.sh --check   # also check current DNS propagation
```

Without these records, Caddy cannot obtain TLS certificates and the dashboard + files relay will be unreachable.

---

### Step 3c — Configure your secrets

Bootstrap step 17/17 prints a **FINAL STEP** block at the end of its output reminding you to fill in `/etc/claude-soma/secrets.env`. Two paths:

#### Option A — manual (nano)

```bash
sudo cp /opt/claude-soma/secrets.env.example /etc/claude-soma/secrets.env
sudo chmod 600 /etc/claude-soma/secrets.env
sudo chown ubuntu:ubuntu /etc/claude-soma/secrets.env
sudo nano /etc/claude-soma/secrets.env   # fill in every required key
sudo bash /opt/claude-soma/scripts/finalize-caddy.sh   # render site configs + reload Caddy
sudo systemctl restart claude-soma-channel.service \
    claude-soma-api.service claude-soma-frontend.service
sudo bash /opt/claude-soma/scripts/smoke_install.sh
```

See the `secrets.env.example` in the repo root for the full key list with inline comments. The required keys are documented in Step 5 of this guide.

#### Option B — Claude copilot (recommended for new installers)

If `claude --version` works on this box (Claude Code CLI installed and authenticated), start a new session and paste the copilot prompt:

```bash
claude
# Once inside the session, paste the contents of:
cat /opt/claude-soma/scripts/env-copilot-prompt.txt
```

The env-copilot-prompt.txt file contains a detailed instruction prompt you paste into a fresh `claude` session. Claude then acts as an interactive secrets-setup copilot: it reads the template, checks what is already filled in, and walks you through each required secret one at a time — explaining what it is, where to obtain it, and writing the value to the file with the right permissions. At the end it validates nothing is missing and offers to restart the services and run the smoke verifier.

To preview the prompt before pasting:

```bash
cat /opt/claude-soma/scripts/env-copilot-prompt.txt
```

---

## Step 4 — Install external CLI binaries (interactive auth)

Each of the following requires a one-time interactive login. The bot needs these on disk to function:

- **claude** (Claude Max OAuth): installed by bootstrap via npm. Run `claude login` once interactively — follow the browser OAuth flow and copy the resulting `oat-...` token into `CLAUDE_CODE_OAUTH_TOKEN` in secrets.env.
- **grok** (xAI): install with `curl -L https://grok.com/cli/install.sh | bash` (or the platform-appropriate method in xAI's docs). Then run `grok login` to authenticate with your xAI account.
- **codex** (OpenAI ChatGPT): install per the instructions at https://github.com/openai/codex. Then run `codex login` to authenticate with your ChatGPT account.
- **hf** (Hugging Face CLI): installed by bootstrap via pip (`huggingface_hub[cli]`). Run `hf auth login` and paste a token from https://huggingface.co/settings/tokens.

These CLIs are referenced by skills (`codex-image-gen`, `grok-image` MCP server) but are not auto-installed because each requires an interactive browser auth flow that cannot run in a non-interactive script.

---

## Step 5 — Provision secrets.env

```bash
sudo cp /opt/claude-soma/secrets.env.example /etc/claude-soma/secrets.env
sudo chown ubuntu:ubuntu /etc/claude-soma/secrets.env
sudo chmod 600 /etc/claude-soma/secrets.env
sudo nano /etc/claude-soma/secrets.env   # fill in all required keys
```

See `secrets.env.example` in the repo root for the full key list with inline comments. Required keys:

| Key | Where to get it |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Step 4 (`claude login`) |
| `SOMA_DOMAIN` | **REQUIRED.** Your base domain — dashboard goes on `soma.<this>` (e.g. `example.com` → dashboard at `soma.example.com`). |
| `FILES_DOMAIN` | _Optional._ Override the relay subdomain (defaults to `files.<SOMA_DOMAIN>`). |
| `ACME_EMAIL` | **REQUIRED.** Let's Encrypt registration email — Caddy uses it to obtain TLS certs and ship expiry warnings. |
| `AUTH_GITHUB_ID` | GitHub OAuth App (Client ID) — see Prerequisites |
| `AUTH_GITHUB_SECRET` | GitHub OAuth App (Client Secret) — see Prerequisites |
| `HERMES_ALLOWED_GITHUB_HANDLES` | your GitHub username (comma-separated for multiple) |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` |
| `NEXTAUTH_URL` | `https://soma.<SOMA_DOMAIN>` (e.g. `https://soma.example.com`) |
| `TELEGRAM_BOT_TOKEN` | @BotFather — see Prerequisites; then run `setup-telegram.sh` |
| `HERMES_NOTIFY_CHAT_ID` | auto-detected by `setup-telegram.sh`, or getUpdates chat.id |
| `TELEGRAM_CHAT_ID` | same value as `HERMES_NOTIFY_CHAT_ID` |
| `HERMES_API_CORS_ORIGINS` | `https://soma.<SOMA_DOMAIN>,http://localhost:3000` |
| `HERMES_FILES_PASSWORD` | choose a strong password (used for basicauth on `files.<SOMA_DOMAIN>`) |

Optional keys (leave commented out to use defaults):

- `HERMES_AUTO_RESTART_WINDOW_UTC` — epoch seconds for auto-restart guard window expiry
- `HERMES_INTERACTIVE_CEILING` — usage-tab ceiling for interactive tokens (renders ceiling cards in dashboard)
- `HERMES_AGENT_SDK_CEILING` — usage-tab ceiling for agent SDK tokens
- `HERMES_ENGAGEMENT_REFILL_THRESHOLD` — engagement drip refill threshold (default: 6)

---

## Step 6 — Finalize Caddy site configs

After filling in `SOMA_DOMAIN`, `ACME_EMAIL`, `HERMES_FILES_PASSWORD` (and optionally `FILES_DOMAIN`) in `secrets.env`, **and** verifying the DNS A records for `soma.<SOMA_DOMAIN>` + `files.<SOMA_DOMAIN>` resolve to this VPS (Let's Encrypt cert acquisition fails if they don't), run:

```bash
sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
```

This script:
- Reads `SOMA_DOMAIN`, `FILES_DOMAIN`, `ACME_EMAIL`, and `HERMES_FILES_PASSWORD` from `/etc/claude-soma/secrets.env`
- Generates the bcrypt hash for the Caddy basicauth block automatically
- Renders `/etc/caddy/Caddyfile` by substituting the `__SOMA_DOMAIN__` + `__ACME_EMAIL__` placeholders shipped in the repo
- Renders `/etc/caddy/conf.d/files.caddyfile` by substituting `__FILES_DOMAIN__` + `__BCRYPT_HASH__`
- Validates the Caddy config and reloads Caddy

Verify: `curl -sI -u "soma:$HERMES_FILES_PASSWORD" https://files.<SOMA_DOMAIN>/ | head -3` — expect HTTP 200.

---

## Step 7 — Restart all the services

After secrets.env is filled in, restart the services so they pick up the new values:

```bash
sudo systemctl restart claude-soma-channel claude-soma-api claude-soma-frontend
```

Verify they are active:

```bash
systemctl is-active claude-soma-channel claude-soma-api claude-soma-frontend claude-soma-markserv
```

---

## Step 8 — Pair the Telegram bot

After `TELEGRAM_BOT_TOKEN` is written to secrets.env and services are restarted (Step 7), run the pairing script:

```bash
bash /opt/claude-soma/scripts/setup-telegram.sh
```

What this does:
1. Reads `TELEGRAM_BOT_TOKEN` from `/etc/claude-soma/secrets.env`.
2. Writes the token to `~/.claude/channels/telegram/.env` so the channel plugin can read it.
3. Prompts you to send any message to your bot from the Telegram app.
4. Polls `getUpdates` to auto-detect your numeric chat ID.
5. Writes your chat ID to `~/.claude/channels/telegram/access.json` (the plugin allowlist).
6. Writes `HERMES_NOTIFY_CHAT_ID` and `TELEGRAM_CHAT_ID` back into `/etc/claude-soma/secrets.env`.
7. Restarts `claude-soma-channel.service` so it picks up the new config.

Manual alternative (if the script is unavailable):

```bash
# 1. Mirror the bot token to the channel plugin config
mkdir -p ~/.claude/channels/telegram
echo "TELEGRAM_BOT_TOKEN=<your-token>" > ~/.claude/channels/telegram/.env
chmod 600 ~/.claude/channels/telegram/.env

# 2. Send a message to your bot from Telegram, then get your chat ID:
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' /etc/claude-soma/secrets.env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" | python3 -c \
  "import json,sys; u=json.load(sys.stdin); print(u['result'][-1]['message']['chat']['id'])"

# 3. Write the chat ID into access.json (replace 0 with your actual ID):
CHAT_ID=<your-chat-id>
cat > ~/.claude/channels/telegram/access.json <<EOF
{"dmPolicy":"allowlist","allowFrom":["${CHAT_ID}"],"groups":{},"pending":{}}
EOF

# 4. Update secrets.env and restart:
sudo sed -i "s|^HERMES_NOTIFY_CHAT_ID=.*|HERMES_NOTIFY_CHAT_ID=${CHAT_ID}|" /etc/claude-soma/secrets.env
sudo sed -i "s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${CHAT_ID}|" /etc/claude-soma/secrets.env
sudo systemctl restart claude-soma-channel.service
```

Verify: send a message to your bot — it should respond. If not, tail the channel log:

```bash
sudo journalctl -u claude-soma-channel -f
```

---

## Step 9 — Smoke verify

```bash
sudo bash scripts/smoke_install.sh
```

See `scripts/smoke_install.sh` for the full check list. It verifies: services active, ports listening, Caddy serving both domains, dashboard HTTP 200, MCPs healthy, timers enabled. All checks must pass before declaring the install complete.

---

## Notes on special components

### claude-safe wrapper

`scripts/claude-safe.sh` is installed to `/usr/local/bin/claude-safe` by bootstrap. It wraps the real `claude` binary and strips the Telegram plugin (`--channels` flag and the telegram plugin opt-in) before invoking claude in project-lead sessions. This prevents a compromised or misbehaving lead from hijacking the orchestrator's Telegram channel.

The wrapper is referenced in the project-lead spawn path (`src/claude_soma/mcp_servers/project_orchestrator/spawner.py`) and the lead systemd template. It must be present at `/usr/local/bin/claude-safe` before any leads are spawned.

Tests: `tests/test_claude_safe_wrapper.py`.

### hermes-notify MCP

The `hermes-notify` MCP server (`src/claude_soma/mcp_servers/hermes_notify/server.py`) provides the `notify_orchestrator` and `set_teammate_handle` tools that let leads send structured lifecycle events (STARTED, MILESTONE, COMPLETED, NEEDS_INPUT, ERROR) back to the orchestrator's Telegram channel.

It is wired into `config/claude/lead-mcp.json` — the MCP config file that every spawned lead inherits. No separate install step is needed beyond ensuring `/opt/claude-soma/.venv` is populated (Step 3 covers this). The server listens on `HERMES_NOTIFY_PORT` (default 9100) and relays events to the orchestrator via the Telegram bot token.

### sudoers grants for lead orchestration (cgroup isolation)

Claude Soma's multi-agent orchestration layer spawns each project lead inside its own **transient systemd service** — `claude-soma-lead-<name>.service` — so it gets a dedicated cgroup. This enables clean teardown: when the channel service restarts (`KillMode=control-group`), it cannot reach leads running in sibling cgroups. Each lead's tmux server is also born inside its own cgroup.

The spawner (`src/claude_soma/mcp_servers/project_orchestrator/spawner.py`) issues these privileged calls:

| Command | Where | Why |
|---|---|---|
| `sudo -n systemd-run --collect --quiet --unit=claude-soma-lead-<name> ...` | `_wrap_in_transient_unit()` (spawner.py:160) | Spawn the transient unit as root so systemd creates a system-level cgroup |
| `sudo -n systemctl stop claude-soma-lead-<name>.service` | `kill_session()` (spawner.py:609) | Tear down the lead's cgroup cleanly on kill |

The bootstrap installs `/etc/sudoers.d/99-claude-soma-spawner` (tracked in the repo at `systemd/sudoers.d/99-claude-soma-spawner`) to grant these operations passwordlessly to the `ubuntu` user. Without this grant, `sudo -n` fails immediately and **all lead spawning is dead on a fresh install**.

The companion file `/etc/sudoers.d/99-claude-soma-restart` (also installed by bootstrap) covers `systemctl restart claude-soma-*.service` for the auto-restart path — a separate concern.

**Security scope:** `systemd-run *` is broad (the spawner passes dozens of `--property=` and `--setenv=` argv), but the transient unit names are always `claude-soma-lead-*` (enforced in spawner code), and the `stop`/`kill`/`reset-failed` grants are tightly scoped to that prefix. This matches the security posture of the existing restart grant.

**Verification:**
```bash
# File installed with strict 0440 root:root permissions:
stat /etc/sudoers.d/99-claude-soma-spawner
# Should print: 0440  root  root  ...

# visudo validates the live file (run as root):
sudo visudo -c -f /etc/sudoers.d/99-claude-soma-spawner
# Should print: /etc/sudoers.d/99-claude-soma-spawner: parsed OK

# Smoke verifier checks the file automatically:
sudo bash scripts/smoke_install.sh
```

**Is cgroup isolation set up automatically?** Yes. On every fresh install, the bootstrap (step 8c/17):
1. Validates the sudoers source file with `visudo -cf` (never installs a broken file).
2. Installs it to `/etc/sudoers.d/99-claude-soma-spawner` with mode 0440 root:root.
3. Re-validates the installed file with `visudo -c -f`.

From that point on, every `spawn_project` call creates a per-lead transient systemd unit via `systemd-run`, which by default uses `KillMode=control-group`. Leads run in their own cgroups, isolated from the channel service.

### relay-cleanup timer

The `claude-soma-relay-cleanup` timer purges old files from `/var/lib/claude-soma/relay/` to prevent unbounded growth. Bootstrap installs and enables the timer. If for any reason it is not enabled on your instance:

```bash
sudo systemctl enable --now claude-soma-relay-cleanup.timer
systemctl is-enabled claude-soma-relay-cleanup.timer   # should print: enabled
```

### Ongoing VPS deploy (post-bootstrap updates)

After the initial bootstrap, the canonical deploy sequence for pulling new code onto the live VPS is:

```bash
git -C /opt/claude-soma pull --ff-only
git -C /opt/claude-soma submodule update --init --recursive   # vendored telegram fork (external/claude-plugins-official)
sudo bash /opt/claude-soma/scripts/deploy-systemd.sh
bash /opt/claude-soma/scripts/build_frontend.sh
sudo systemctl restart claude-soma-frontend.service
```

The `submodule update` line keeps the vendored Telegram plugin fork (`external/claude-plugins-official`) checked out at the commit pinned by the repo. It is a no-op when nothing changed; it is REQUIRED whenever the submodule pointer moves (e.g. a new reply-to/plugin patch). Activating a moved telegram submodule additionally needs an operator-gated `sudo systemctl restart claude-soma-channel.service` — see [docs/telegram-plugin-fork.md](docs/telegram-plugin-fork.md).

`deploy-systemd.sh` syncs any changed `systemd/*.{service,timer}` files from the repo to `/etc/systemd/system` and runs `daemon-reload`. Changed timers are auto-restarted; changed `.service` files print `RESTART REQUIRED` so the operator can restart them at a safe moment. `claude-soma-channel.service` is always operator-gated and will never be auto-restarted by the script (restarting the bot from a script it invoked would kill the calling process). Run `--dry-run` to preview changes without applying them.

---

## Troubleshooting

### `files.<your-domain>` returns 502 or is unreachable

- Confirm `files.caddyfile` is installed: `ls /etc/caddy/conf.d/files.caddyfile`
- Confirm `/etc/caddy/Caddyfile` contains `import /etc/caddy/conf.d/*.caddyfile` at the bottom
- Confirm `markserv` is installed: `which markserv`
- Confirm the markserv service is active: `systemctl status claude-soma-markserv`
- Confirm the bcrypt hash was updated in `files.caddyfile` (Step 6)

### Dashboard 500 / NextAuth errors

- Confirm `NEXTAUTH_SECRET` is set in secrets.env
- Confirm `NEXTAUTH_URL` matches `https://soma.<your-domain>` exactly
- Confirm GitHub OAuth app callback URL matches `https://soma.<your-domain>/api/auth/callback/github` exactly
- Restart the frontend: `sudo systemctl restart claude-soma-frontend`
- Tail logs: `sudo journalctl -u claude-soma-frontend -f`

### Bot won't start or doesn't reply

- Confirm `CLAUDE_CODE_OAUTH_TOKEN` is set in secrets.env
- Confirm `TELEGRAM_BOT_TOKEN` is set in secrets.env (or at `~/.claude/channels/telegram/.env` as fallback)
- Confirm `bun` is installed: `which bun` (should be `/usr/local/bin/bun` or `/home/ubuntu/.bun/bin/bun`). If missing: `sudo -u ubuntu bash -c 'curl -fsSL https://bun.sh/install | bash' && sudo ln -sf /home/ubuntu/.bun/bin/bun /usr/local/bin/bun`
- Tail the channel journal: `sudo journalctl -u claude-soma-channel -f`
- Attach the channel tmux: `somux a channel`

### Engagement drip doesn't fire

- Confirm `/var/lib/claude-soma/engagement/queue.jsonl` exists and is owned by ubuntu: `ls -la /var/lib/claude-soma/engagement/`
- Confirm the timer is enabled: `systemctl is-enabled claude-soma-engagement-drip.timer`
- Check the script: `cat /opt/claude-soma/scripts/engagement-hourly-drip.py`
- Manually trigger a run: `sudo systemctl start claude-soma-engagement-drip.service` and tail the journal

### OCI: Caddy serving locally but unreachable from internet

- The iptables ACCEPT rules for ports 80/443 must come BEFORE Oracle's default REJECT rule. Run bootstrap with `--cloud=oci` (Step 3) to apply the fix automatically.
- Verify: `sudo iptables -L INPUT --line-numbers -n | grep -E 'ACCEPT|REJECT'` — ACCEPT entries for ports 80/443 must appear before the REJECT entry.
