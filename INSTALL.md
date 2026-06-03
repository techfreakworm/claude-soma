# INSTALL.md — claude-soma fresh-VPS install runbook

This is the canonical install runbook for bringing claude-soma up on a fresh Ubuntu 24.04 VPS.

For a quickstart, see README.md. For an architecture overview, see
`docs/superpowers/specs/2026-05-22-hermes-claude-design.md`.

---

## Prerequisites

External accounts you must provision before starting. Have these values ready in a notes file:

1. **Claude Max OAuth** — sign up at claude.ai; run `claude` once interactively on any browser-capable machine to mint `CLAUDE_CODE_OAUTH_TOKEN`. Copy the `oat-...` token.
2. **GitHub OAuth App** — create at `https://github.com/settings/developers`. Set callback URL to `https://soma.<your-domain>/api/auth/callback/github`. Save `AUTH_GITHUB_CLIENT_ID` + `AUTH_GITHUB_CLIENT_SECRET`.
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
- Installs the Caddyfile to `/etc/caddy/Caddyfile` (with `import /etc/caddy/conf.d/*.caddyfile` at the bottom) and the files relay config to `/etc/caddy/conf.d/files.caddyfile`; reloads Caddy
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
| `AUTH_GITHUB_CLIENT_ID` | Step 0, GitHub OAuth App |
| `AUTH_GITHUB_CLIENT_SECRET` | Step 0, GitHub OAuth App |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` |
| `NEXTAUTH_URL` | `https://soma.<your-domain>` |
| `AUTH_GITHUB_OWNER` | your GitHub username |
| `TELEGRAM_BOT_TOKEN` | Step 0, @BotFather |
| `HERMES_NOTIFY_CHAT_ID` | Step 0, getUpdates chat.id |
| `HERMES_FILES_PASSWORD` | choose a strong password (used for basicauth on files domain) |

Optional keys (leave commented out to use defaults):

- `HERMES_AUTO_RESTART_WINDOW_UTC` — epoch seconds for auto-restart guard window expiry
- `HERMES_INTERACTIVE_CEILING` — usage-tab ceiling for interactive tokens (renders ceiling cards in dashboard)
- `HERMES_AGENT_SDK_CEILING` — usage-tab ceiling for agent SDK tokens
- `HERMES_ENGAGEMENT_REFILL_THRESHOLD` — engagement drip refill threshold (default: 6)

---

## Step 6 — Generate the files domain bcrypt hash

After setting `HERMES_FILES_PASSWORD` in secrets.env, generate the bcrypt hash for the Caddy basicauth block:

```bash
source /etc/claude-soma/secrets.env
caddy hash-password --plaintext "$HERMES_FILES_PASSWORD"
```

Copy the output hash. Edit `/etc/caddy/conf.d/files.caddyfile` and replace the placeholder hash with the output. Then reload Caddy:

```bash
sudo systemctl reload caddy.service
```

Verify: `curl -sI -u "soma:$HERMES_FILES_PASSWORD" https://files.<your-domain>/ | head -3` — expect HTTP 200.

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

Open Telegram and DM your bot. The channel session picks up the chat ID automatically via the Telegram plugin pairing flow. If prompted for a pairing code:

```bash
# Attach to the channel session to confirm pairing:
somux a channel   # or: tmux -S /tmp/claude-soma-channel.sock attach
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

### relay-cleanup timer

The `claude-soma-relay-cleanup` timer purges old files from `/var/lib/claude-soma/relay/` to prevent unbounded growth. Bootstrap installs and enables the timer. If for any reason it is not enabled on your instance:

```bash
sudo systemctl enable --now claude-soma-relay-cleanup.timer
systemctl is-enabled claude-soma-relay-cleanup.timer   # should print: enabled
```

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
